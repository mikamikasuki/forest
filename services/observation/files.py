"""Bounded scope reconciliation and generation-checked source resolution.

A scan is a recoverable latency hint, not a complete history of external edits.
Current small-file bytes are compared directly, including unchanged stat tuples.
No historical secret archive, scientific hash identity, or unsafe deserialization.
"""
import os
import json
import stat
import time
import heapq
from pathlib import Path, PurePosixPath
from sqlalchemy import select, func, update, tuple_, or_, literal
from services.api.db import Session, Project, Branch, Node, TaskRun, PaperDocument, Figure, now, uid
from services.api.common import project_dir, get, error
from .models import ObservationState, ObservationScope, ObservedFile
from .segments import index
from .metadata import inspect
from .contracts import ScopeCoverage, FileObservation, SourceRef, SourceView

CONTENT_LIMIT = 256*1024
PROJECT_CONTENT_LIMIT = 32*1024*1024
PROJECT_FILE_LIMIT = 100000
ENTRIES_PER_PASS = 200
BYTES_PER_PASS = 2*1024*1024
EXCLUDED = {'node_modules', '__pycache__', '.git', '.venv', 'venv', 'secrets.json', 'credentials.json', 'owner-token', 'agent_session.json', 'model_context', 'planning_sessions', 'model_responses'}

def observation_write_lock(s,project_id):
    """Parent before derived rows; one bounded batch, never a whole scan.

    Serializing generation allocation prevents different bytes sharing a
    generation and prevents cascade deletion / child-insert lock inversion.
    """
    from .reporting import write_lock
    write_lock(s)
    transaction=s.get_transaction()
    locked=s.info.setdefault('observation_write_locks',{})
    if project_id in locked and locked[project_id] is transaction: return
    with s.no_autoflush: get(s,Project,project_id,for_update=True)
    locked[project_id]=s.get_transaction()

def allowed(path):
    parts=PurePosixPath(path).parts
    return bool(parts) and not PurePosixPath(path).is_absolute() and all(
        v not in ('.','..') and not v.startswith('.') and v.lower() not in EXCLUDED and
        not v.lower().endswith(('.pem','.key','.p12','.pfx')) for v in parts)

def regular_read(root: Path, relative: str, limit=CONTENT_LIMIT, *, project_root=None):
    """Open every relative component without following symlinks, then compare reads."""
    if not allowed(relative): raise PermissionError('Excluded source')
    descriptors=[]; directories=[]
    try:
        anchor=project_root if project_root is not None else root
        fd=os.open(anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); descriptors.append(fd)
        # Pin the project before walking a branch/run root. O_NOFOLLOW on an
        # absolute root alone would still follow a raced ancestor symlink.
        for part in root.relative_to(anchor).parts:
            parent=fd
            fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            descriptors.append(fd);directories.append((parent,part,fd))
        parts=PurePosixPath(relative).parts
        for part in parts[:-1]:
            parent=fd
            fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            descriptors.append(fd);directories.append((parent,part,fd))
        f=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd); descriptors.append(f)
        before=os.fstat(f)
        if not stat.S_ISREG(before.st_mode): raise PermissionError('Not a regular file')
        data=None
        if before.st_size<=limit:
            chunks=[]; remaining=limit+1
            while remaining:
                part=os.read(f,min(remaining,65536))
                if not part: break
                chunks.append(part); remaining-=len(part)
            data=b''.join(chunks)
            if len(data)>limit: data=None
            # A second direct read catches small writes even when mtime is restored.
            os.lseek(f,0,os.SEEK_SET)
            again=b''
            if data is not None:
                while len(again)<=limit:
                    part=os.read(f,min(65536,limit+1-len(again)))
                    if not part: break
                    again+=part
                if again!=data: raise BlockingIOError('Source changing')
        metadata=inspect(f,before.st_size,relative,data)
        after=os.fstat(f)
        final=os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)
        def identity(s): return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if identity(before)!=identity(after) or identity(after)!=identity(final): raise BlockingIOError('Source changing')
        saved_anchor=os.fstat(descriptors[0]);current_anchor=os.stat(anchor,follow_symlinks=False)
        if (saved_anchor.st_dev,saved_anchor.st_ino)!=(current_anchor.st_dev,current_anchor.st_ino): raise BlockingIOError('Project directory changing')
        for parent,part,directory in directories:
            saved=os.fstat(directory);current=os.stat(part,dir_fd=parent,follow_symlinks=False)
            if (saved.st_dev,saved.st_ino)!=(current.st_dev,current.st_ino): raise BlockingIOError('Source directory changing')
        return data, {'size':after.st_size,'mtime_ns':after.st_mtime_ns,'ctime_ns':after.st_ctime_ns,'inode':after.st_ino,'metadata':metadata}
    finally:
        for fd in reversed(descriptors): os.close(fd)

def scope_root(scope):
    root=project_dir(scope.project_id)
    parts=PurePosixPath(scope.root).parts
    if PurePosixPath(scope.root).is_absolute() or '..' in parts: raise PermissionError('Invalid scope')
    candidate=root.joinpath(*parts)
    if any(p.is_symlink() for p in [candidate,*candidate.parents] if p!=root.parent and p.is_relative_to(root)):
        raise PermissionError('Symlink scope')
    if not candidate.resolve().is_relative_to(root.resolve()): raise PermissionError('Invalid scope')
    return candidate

def scope_authorized(s, scope):
    get(s,Project,scope.project_id)
    if scope.kind=='project': return scope.object_id==scope.project_id and scope.root==''
    if scope.kind=='branch_workspace':
        b=s.get(Branch,scope.object_id)
        return bool(b and b.project_id==scope.project_id and b.workspace==scope.root)
    r=s.get(TaskRun,scope.object_id)
    if not r or r.project_id!=scope.project_id: return False
    suffix='/workspace' if scope.kind=='run_workspace' else ''
    if scope.kind=='remote_workspace': return bool(r.config.get('remote'))
    return scope.root==r.output_path+suffix

def sync_scopes(s, project_id, *, cursor=None, recent_only=False, related_path=None):
    """Register current owners in DB; no paths inferred from file basenames."""
    roots=[('project',project_id,'',None)]
    roots.extend(('branch_workspace',b.id,b.workspace,None) for b in s.scalars(select(Branch).where(Branch.project_id==project_id)))
    # DB-only registration; historical inventory work is scheduled fairly later.
    query=select(TaskRun).where(TaskRun.project_id==project_id)
    page=[]
    if cursor is not None:
        page=list(s.scalars(query.where(TaskRun.id>cursor).order_by(TaskRun.id).limit(200)))
    if recent_only or cursor is not None:
        latest=list(s.scalars(query.order_by(TaskRun.created_at.desc()).limit(100)))
        active=list(s.scalars(query.where(TaskRun.status.in_(['queued','running','pausing','paused','waiting','waiting_input'])).limit(100)))
        related=list(s.scalars(query.where(TaskRun.output_path!='',or_(
            TaskRun.output_path==related_path,
            func.substr(literal(related_path),1,func.length(TaskRun.output_path)+1)==TaskRun.output_path+'/')).limit(200))) if related_path else []
        runs={r.id:r for r in [*page,*latest,*active,*related]}.values()
    else: runs=s.scalars(query)
    for r in runs:
        if not r.output_path: continue
        attempt=(r.config.get('execution_attempt') or {}).get('id')
        roots.extend([('run_output',r.id,r.output_path,attempt),('run_workspace',r.id,r.output_path+'/workspace',attempt)])
        if r.config.get('remote'): roots.append(('remote_workspace',r.id,r.output_path,attempt))
    existing_query=select(ObservationScope).where(ObservationScope.project_id==project_id)
    if recent_only or cursor is not None:
        existing_query=existing_query.where(tuple_(ObservationScope.kind,ObservationScope.object_id).in_([(k,o) for k,o,_,_ in roots]))
    existing={(v.kind,v.object_id):v for v in s.scalars(existing_query)}
    for kind,obj,root,attempt in roots:
        row=existing.pop((kind,obj),None)
        if row is None:
            s.add(ObservationScope(project_id=project_id,kind=kind,object_id=obj,root=root,attempt_id=attempt))
        elif row.root!=root or row.attempt_id!=attempt:
            row.root=root; row.attempt_id=attempt; row.coverage='pending'; row.queue=[['','']]
            # A new attempt at the same path is not the previous attempt's content.
            s.execute(update(ObservedFile).where(ObservedFile.scope_id==row.id).values(
                generation=ObservedFile.generation+1,content=None,segments=[],state='unavailable',
                attempt_id=attempt,stat={},parse_state='unavailable'))
            s.info.pop(('observation_content_bytes',project_id),None)
    if not recent_only and cursor is None:
        for row in existing.values(): s.delete(row)
    return page[-1].id if len(page)==200 else ''

def observe(s, scope, relative, scan_generation, attribution='external_observation', action_id=None):
    observation_write_lock(s,scope.project_id)
    row=s.scalar(select(ObservedFile).where(ObservedFile.scope_id==scope.id,ObservedFile.path==relative))
    old_state=row.state if row else None
    if row is None:
        key=('observation_file_count',scope.project_id)
        if key not in s.info:
            s.info[key]=s.scalar(select(func.count()).select_from(ObservedFile).where(ObservedFile.project_id==scope.project_id)) or 0
        if s.info[key]>=PROJECT_FILE_LIMIT:
            scope.error='Project inventory capacity reached; additional files are not indexed'; scope.coverage='partial'
            return None
        s.info[key]+=1
        row=ObservedFile(id=uid(),project_id=scope.project_id,scope_id=scope.id,path=relative,
                         generation=0,scan_generation=scan_generation); s.add(row)
    try:
        data,metadata=regular_read(scope_root(scope),relative,project_root=project_dir(scope.project_id))
        state='available' if data is not None else 'metadata_only'
        key=('observation_content_bytes',scope.project_id)
        if key not in s.info:
            s.info[key]=s.scalar(select(func.coalesce(func.sum(func.length(ObservedFile.content)),0)).where(ObservedFile.project_id==scope.project_id)) or 0
        total=s.info[key]
        if data is not None and total-len(row.content or b'')+len(data)>PROJECT_CONTENT_LIMIT:
            data=None; state='metadata_only'
        changed=(old_state!=state or data!=row.content or metadata!=row.stat or row.attempt_id!=scope.attempt_id)
        if changed:
            s.info[key]=total-len(row.content or b'')+len(data or b'')
            row.generation=(row.generation or 0)+1; row.content=data
            row.segments,row.parse_state=index(data,relative) if data is not None else ([], 'metadata_only')
            row.attribution=attribution; row.action_id=action_id
        row.stat=metadata; row.size=metadata['size']; row.state=state
    except FileNotFoundError:
        if old_state!='deleted': row.generation=(row.generation or 0)+1
        row.state='deleted'; row.content=None; row.segments=[]; row.parse_state='unavailable'
        row.stat={k:v for k,v in (row.stat or {}).items() if k!='metadata'}
    except BlockingIOError:
        row.state='changing'; row.content=None; row.segments=[]; row.parse_state='partial'
        row.stat={k:v for k,v in (row.stat or {}).items() if k!='metadata'}
    except OSError:
        row.state='unavailable'; row.content=None; row.segments=[]; row.parse_state='unavailable'
        row.stat={k:v for k,v in (row.stat or {}).items() if k!='metadata'}
    row.observed_at=now(); row.scan_generation=scan_generation; row.attempt_id=scope.attempt_id
    return row

def managed_change(s, project_id, project_path, attribution, action_id=None):
    """Successful managed publication only; the reconciler repairs missed DB commits."""
    if not allowed(project_path): return
    observation_write_lock(s,project_id)
    # A first edit/tool write can precede the observer's registration tick.
    # Register known owners before capturing, so attribution is not lost and
    # later reconstructed as an anonymous external mutation.
    sync_scopes(s,project_id,recent_only=True,related_path=project_path)
    candidates=select(ObservationScope).where(ObservationScope.project_id==project_id,ObservationScope.kind!='remote_workspace',
        or_(ObservationScope.root=='',func.substr(literal(project_path),1,func.length(ObservationScope.root)+1)==ObservationScope.root+'/'))
    for scope in s.scalars(candidates):
        if scope.kind=='remote_workspace': continue
        prefix=scope.root.rstrip('/')+'/' if scope.root else ''
        if not project_path.startswith(prefix): continue
        relative=project_path[len(prefix):]
        if scope.kind=='project' and relative.split('/')[0] in ('branches','runs'): continue
        if scope.kind=='run_output' and relative.startswith('workspace/'): continue
        if relative and allowed(relative): observe(s,scope,relative,scope.scan_generation,attribution,action_id)

def reconcile(scope_id):
    """At most 200 entries / 2 MiB content per pass, resumable directory cursor."""
    with Session.begin() as s:
        scope=s.get(ObservationScope,scope_id)
        if not scope or not scope_authorized(s,scope): return
        observation_write_lock(s,scope.project_id)
        s.refresh(scope)
        scope.observed_at=now()
        if scope.kind=='remote_workspace':
            scope.coverage='unavailable'; scope.error='Remote observations are not local inventory'; return
        try: root=scope_root(scope)
        except OSError:
            scope.coverage='unavailable'; scope.error='Scope unavailable'; return
        if not root.is_dir():
            scope.coverage='unavailable'; scope.error='Workspace not collected or not created'; return
        if not scope.queue or scope.coverage in ('pending','unavailable'):
            scope.scan_generation+=1; scope.queue=[['','']]
            scope.error=None
        generation=scope.scan_generation; queue=[list(v) for v in scope.queue]
        consumed=0; byte_count=0
        while queue and consumed<ENTRIES_PER_PASS and byte_count<BYTES_PER_PASS:
            directory,cursor=queue[0]
            folder=root/directory
            try:
                if folder.is_symlink() or not folder.resolve().is_relative_to(root.resolve()): raise PermissionError()
                with os.scandir(folder) as iterator:
                    entries=heapq.nsmallest(ENTRIES_PER_PASS-consumed+1,(e for e in iterator if e.name>cursor),key=lambda e:e.name)
            except OSError:
                scope.error='Directory unavailable during scan'; queue.pop(0); continue
            exhausted=True
            for entry in entries:
                relative=(PurePosixPath(directory)/entry.name).as_posix()
                queue[0][1]=entry.name; consumed+=1
                excluded=(not allowed(relative) or entry.is_symlink() or
                          scope.kind=='project' and entry.name in ('branches','runs') and not directory or
                          scope.kind=='run_output' and entry.name=='workspace' and not directory)
                if not excluded:
                    if entry.is_dir(follow_symlinks=False):
                        if len(queue)<2048: queue.append([relative,''])
                        else: scope.error='Directory queue capacity reached; inventory is incomplete'
                    elif entry.is_file(follow_symlinks=False):
                        f=observe(s,scope,relative,generation)
                        if f: byte_count+=min(f.size,CONTENT_LIMIT)
                if consumed>=ENTRIES_PER_PASS or byte_count>=BYTES_PER_PASS: exhausted=False; break
            if exhausted: queue.pop(0)
        scope.queue=queue; scope.coverage='partial' if queue or scope.error else 'complete'; scope.observed_at=now()
        if not queue and not scope.error:
            s.execute(update(ObservedFile).where(ObservedFile.scope_id==scope.id,
                ObservedFile.scan_generation!=generation,ObservedFile.state!='deleted').values(
                state='deleted',content=None,segments=[],stat={},parse_state='unavailable',generation=ObservedFile.generation+1,observed_at=now()))

def coverage(s,scope):
    count=s.scalar(select(func.count()).select_from(ObservedFile).where(ObservedFile.scope_id==scope.id,ObservedFile.state!='deleted')) or 0
    return ScopeCoverage(id=scope.id,kind=scope.kind,object_id=scope.object_id,attempt_id=scope.attempt_id,
        scan_generation=scope.scan_generation,coverage=scope.coverage,observed_at=scope.observed_at,error=scope.error,total=count)

def reference(row,scope,epoch=None):
    if epoch is None:
        with Session() as s:
            state=s.get(ObservationState,row.project_id)
            epoch=state.epoch if state else None
    return SourceRef(epoch=epoch,project_id=row.project_id,kind='file',object_id=row.id,scope_id=scope.id,path=row.path,
                     file_generation=row.generation,attempt_id=row.attempt_id)

def file_view(row,scope,epoch=None):
    return FileObservation(id=row.id,scope_id=row.scope_id,path=row.path,generation=row.generation,state=row.state,
        size=row.size,parse_state=row.parse_state,attribution=row.attribution,observed_at=row.observed_at,source=reference(row,scope,epoch))

def resolve(source):
    with Session() as s:
        get(s,Project,source.project_id)
        state=s.get(ObservationState,source.project_id)
        if not state or source.epoch!=state.epoch:
            return SourceView(source=source,availability='changed',parse_state='unavailable',note='Data epoch changed; resolve a fresh source reference.')
        if source.kind!='file':
            model={'project':Project,'node':Node,'run':TaskRun,'paper':PaperDocument,'figure':Figure}[source.kind]
            obj=get(s,model,source.object_id)
            owner=obj.id if source.kind=='project' else obj.project_id
            if owner!=source.project_id: error('SOURCE_SCOPE','Source does not belong to this project',404)
            revision=obj.node_revision if source.kind=='run' else obj.revision
            attempt=(obj.config.get('execution_attempt') or {}).get('id') if source.kind=='run' else None
            if (source.revision!=revision or source.attempt_id!=attempt
                    or source.record_updated_at is not None and source.record_updated_at!=obj.updated_at):
                return SourceView(source=source,availability='changed',parse_state='record',note='Referenced record revision or attempt changed.')
            fields={'project':['id','revision','mode'], 'node':['id','revision','execution_status','research_status','deliverable_status'],
                    'run':['id','node_id','branch_id','node_revision','status','started_at','finished_at','exit_code'],
                    'paper':['id','revision','status'], 'figure':['id','revision','status']}[source.kind]
            value={field:getattr(obj,field) for field in fields}
            if source.kind=='figure':
                from .scientific import figure_record
                value.update(figure_record(obj))
            elif source.kind=='project':
                from .scientific import delivery_record
                value['last_recorded_delivery_audit']=delivery_record(obj)
            return SourceView(source=source,availability='available',content=json.dumps(value,indent=2),parse_state='record',
                              observed_at=obj.updated_at,note='Persisted source record. Execution completion is separate from scientific verification.')
        row=s.get(ObservedFile,source.object_id)
        if row is None:
            return SourceView(source=source,availability='unavailable',parse_state='unavailable',note='Source observation was retired or is not available in this epoch.')
        if row.project_id!=source.project_id or row.scope_id!=source.scope_id: error('SOURCE_SCOPE','Source does not belong to this project and scope',404)
        if source.path!=row.path: error('SOURCE_SCOPE','Source path does not match its recorded identity',422)
        scope=get(s,ObservationScope,row.scope_id)
        if not scope_authorized(s,scope): error('SOURCE_SCOPE','Source owner is unavailable',404)
        def result(state,note,content=None,segments=None):
            return SourceView(source=source,availability=state,content=content,segments=segments or [],parse_state=row.parse_state,
                observed_at=row.observed_at,note=note,
                metadata=(row.stat or {}).get('metadata',{}) if state in ('available','unavailable') else {},
                artifact_url=f'/api/projects/{row.project_id}/progress/artifacts/{row.id}?epoch={source.epoch}&generation={row.generation}'
                    if state=='available' and scope.kind!='remote_workspace' and row.content is not None else None,
                project_path=None if scope.kind=='remote_workspace' else (PurePosixPath(scope.root)/row.path).as_posix())
        if row.state=='deleted': return result('deleted','Source was deleted; retained derived bytes were cleared.')
        if source.file_generation!=row.generation or source.attempt_id!=row.attempt_id: return result('changed','Source generation changed. Historical bytes are not retained.')
        if scope.kind!='remote_workspace':
            try: current,metadata=regular_read(scope_root(scope),row.path,project_root=project_dir(scope.project_id))
            except FileNotFoundError: return result('deleted','Source was deleted; no retained bytes are returned.')
            except OSError: return result('unavailable','Source cannot be safely read now.')
            if current!=row.content: return result('changed','Current bytes differ from the observed generation. Wait for reconciliation.')
            if row.content is None and metadata!=row.stat:
                return result('changed','Metadata changed after observation. Wait for reconciliation; full content is not retained.')
        if row.content is None: return result('unavailable','Bounded metadata/sample only; full content was not observed or retained. Metadata is dated, not a current-content assertion.')
        if source.segment_id and source.segment_id.startswith('pdf_page:'):
            try: page=int(source.segment_id.removeprefix('pdf_page:'))
            except ValueError: page=0
            pages=(row.stat or {}).get('metadata',{}).get('pages',0)
            if not row.path.lower().endswith('.pdf') or not 1<=page<=pages:
                return result('unavailable','Page is outside the observed PDF artifact.')
            return result('available',f'Observed PDF page {page} of {pages}; page metadata and rendering do not verify scientific claims.')
        try: text=row.content.decode('utf-8')
        except UnicodeDecodeError:
            if source.segment_id: return result('unavailable','Binary section byte locations are not available; open the whole observed artifact.')
            return result('available','Exact observed binary bytes; header metadata is not scientific verification. Open the generation-bound artifact.')
        segments=row.segments
        if source.segment_id:
            segment=next((v for v in segments if v['id']==source.segment_id),None)
            if not segment: return result('unavailable','Section does not belong to this generation.')
            text=row.content[segment['start_byte']:segment['end_byte']].decode('utf-8'); segments=[segment]
        note='Saved remote bytes at the displayed observation time; live remote currentness is unknown. '+(scope.error or 'No SSH is performed by source reads.') if scope.kind=='remote_workspace' else 'Exact current observed bytes; scientific claims are not verified by this view.'
        return result('available',note,text,segments)
