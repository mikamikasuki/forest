"""Reporting-domain regressions in isolated real databases and API handlers.

Fixture rows test history/interleavings; real worker/browser qualification is
separate. No fixture or injected provider is counted as live model evidence.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import pytest
from services.observation.segments import index

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('path,text,expected,state',[
    ('model.py','class 模型:\r\n    def fit(self):\r\n        return "结果"\r\n','模型.fit','parsed'),
    ('notes.md','# A\r\n结果\r\n# A\r\nnext\r\n','A [2]','parsed'),
    ('metrics.json','{"a/b":{"~value":"结果"}}','/a~1b/~0value','parsed'),
    ('data.yaml','a:\n  b: 结果\n','/a/b','parsed'),
    ('bad.py','def unfinished(', 'Whole file','parse_error'),
    ('code.tsx','export const C = () => <div />;', 'Whole file','raw_fallback'),
])
def test_byte_locations(path,text,expected,state):
    raw=text.encode(); segments,actual=index(raw,path)
    assert actual==state
    selected=next(s for s in segments if s['name']==expected)
    assert raw[selected['start_byte']:selected['end_byte']].decode()
    assert selected['start_line']>=1


def test_python_symbol_ranges_ignore_form_feed_as_line_break():
    raw=b'def a():\n    pass\n\x0c\ndef b():\n    pass\n'
    segments,state=index(raw,'x.py')
    assert state=='parsed'
    symbol=next(s for s in segments if s['name']=='b')
    assert raw[symbol['start_byte']:symbol['end_byte']]==b'def b():\n    pass'
    assert (symbol['start_line'],symbol['end_line'])==(4,5)


def scenario():
    import time
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from sqlalchemy import select,func,update
    from services.api.main import app
    from services.api.db import migrate,Session,Project,Branch,TaskRun,Node,PaperDocument,Figure,ModelRequest,Provider,uid
    from services.api.common import project_dir,emit
    from services.observation.models import ObservationState,ObservationScope,ObservedFile,ReportJob,ReporterPolicy
    from services.observation.files import sync_scopes,reconcile,reference,resolve,observe
    from services.observation.reporting import configure,enqueue,claim,execute,write_lock,ensure_state
    from services.observation.service import refresh_projection
    from services.observation.contracts import SourceRef,ReporterSettingsWrite,ReporterSettings
    from services.observation.projection import project_snapshot,coherent_read
    migrate();migrate()
    client=TestClient(app)  # Deliberately no background scheduler in the race fixture.
    def ok(response):
        assert response.status_code==200,response.text
        return response.json()
    p=ok(client.post('/api/projects',json={'name':'Observation fixture'}));pid=p['id'];root=project_dir(pid)
    empty=ok(client.get(f'/api/projects/{pid}/progress'))
    assert empty['run_counts']=={} and not empty['health']['history_gap']
    assert client.get(f'/api/projects/{pid}/reports/latest').json() is None
    with Session.begin() as s:
        state=ensure_state(s,pid);sync_scopes(s,pid)
        branch=s.scalar(select(Branch).where(Branch.project_id==pid));bid=branch.id;workspace=branch.workspace
        n=Node(project_id=pid,branch_id=bid,title='History');s.add(n);s.flush();nid=n.id
        old=TaskRun(id=uid(),project_id=pid,node_id=nid,branch_id=bid,request_id='old',status='completed',output_path='runs/old',node_revision=0)
        new=TaskRun(id=uid(),project_id=pid,node_id=nid,branch_id=bid,request_id='new',status='failed',output_path='runs/new',node_revision=0)
        s.add_all([old,new]);n.extra={'latest_run_id':new.id};s.flush();sync_scopes(s,pid)
        paper=PaperDocument(project_id=pid,title='Unchanged',revision=7,status='compiled',data={'compiled_revision':6});s.add(paper);s.flush();paperid=paper.id
        figure=Figure(project_id=pid,title='Historical source figure',status='ready_for_review',
            data={'source_run_ids':[old.id],'visual_review_status':'selected'});s.add(figure);s.flush();figureid=figure.id
        s.get(Project,pid).config={'controller':{'submission_quality':{'status':'ready','gaps':[]}}}
        before=(s.get(Project,pid).revision,s.get(Node,nid).revision,dict(paper.data),paper.status,s.scalar(select(func.count()).select_from(TaskRun)))
        scopes=list(s.scalars(select(ObservationScope).where(ObservationScope.project_id==pid)))
        ids={scope.kind+':'+scope.object_id:scope.id for scope in scopes}
    owner='regression-owner';refresh_projection(pid,owner,release=True)
    snap=ok(client.get(f'/api/projects/{pid}/progress'))
    facts={f['id']:f for f in snap['facts']}
    assert facts['run:'+old.id]['applicability']=='historical'
    assert facts['run:'+new.id]['applicability']=='current' and facts['run:'+new.id]['value']=='failed'
    assert 'compiled revision 6' in facts['paper:'+paperid]['value']
    assert facts['figure:'+figureid]['applicability']=='historical'
    assert 'visual review selected' in facts['figure:'+figureid]['value']
    assert facts['delivery:'+pid]['applicability']=='not_checked'
    figure_ref=SourceRef.model_validate(facts['figure:'+figureid]['sources'][0])
    delivery_ref=SourceRef.model_validate(facts['delivery:'+pid]['sources'][0])
    assert json.loads(resolve(figure_ref).content)['source_run_ids']==[old.id]
    assert json.loads(resolve(delivery_ref).content)['last_recorded_delivery_audit']['current_applicability']=='not_checked'
    with Session.begin() as s:
        # Rendering updates data without incrementing the editable figure revision.
        s.get(Figure,figureid).data={'source_run_ids':[new.id],'visual_review_status':'rejected'}
    assert resolve(figure_ref).availability=='changed'
    assert client.post(f'/api/projects/{pid}/progress/source',json={**figure_ref.model_dump(),'project_id':uid()}).status_code==422
    # A busy background observer cannot hold an event-invalidated API cache
    # stale while rotating unrelated projects/scopes.
    from datetime import datetime,timezone,timedelta
    with Session.begin() as s:
        cached=s.get(ObservationState,pid);cached.lease_owner='busy-background';cached.lease_until=time.time()+30
        cached.observed_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        project=s.get(Project,pid);project.config={**project.config,'controller':{**project.config['controller'],'status':'paused'}}
        emit(s,pid,'controller_changed',{})
    invalidated=ok(client.get(f'/api/projects/{pid}/progress'))
    assert invalidated['controller_status']=='paused' and invalidated['cursor']>snap['cursor']
    for status,section in [('queued','next'),('waiting','blocked'),('waiting_input','attention'),('paused','now'),('budget_exhausted','attention')]:
        with Session.begin() as s:
            run=s.get(TaskRun,new.id);run.status=status;run.resource={'blocked_reason':'dependency_failed'}
        refresh_projection(pid,owner,release=True)
        current=ok(client.get(f'/api/projects/{pid}/progress'))
        fact=next(f for f in current['facts'] if f['id']=='run:'+new.id)
        assert fact['section']==section and fact['value']==status
        if status!='queued':assert any(f['id']=='reason:'+new.id for f in current['facts'])
    with Session.begin() as s:s.get(TaskRun,new.id).status='failed'
    with Session.begin() as s:
        for i in range(650):emit(s,pid,'run_changed',{'test_invalidation':i})
    refresh_projection(pid,owner,release=True)
    assert ok(client.get(f'/api/projects/{pid}/progress'))['health']['history_gap']
    refresh_projection(pid,owner,release=True)
    assert ok(client.get(f'/api/projects/{pid}/progress'))['health']['history_gap']
    # Three identical relative paths are three source objects.
    for directory,value in [(workspace,'# Same\nbranch'),('runs/old/workspace','# Same\nold'),('runs/new/workspace','# Same\nnew')]:
        path=root/directory/'name # ? 结果.md';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
    for scope_id in ids.values(): reconcile(scope_id)
    with Session() as s:
        files=list(s.scalars(select(ObservedFile).where(ObservedFile.project_id==pid,ObservedFile.path=='name # ? 结果.md')))
        assert len(files)==3
        f=next(f for f in files if f.scope_id==ids['branch_workspace:'+bid]);scope=s.get(ObservationScope,f.scope_id);ref=reference(f,scope)
        segments=f.segments;generation=f.generation
    assert resolve(ref).availability=='available'
    changed=root/workspace/ref.path;st=changed.stat();changed.write_text('# Same\nBRANCH');os.utime(changed,ns=(st.st_atime_ns,st.st_mtime_ns))
    # A stale link fails even before watcher/reconciliation.
    assert resolve(ref).availability=='changed'
    reconcile(ref.scope_id)
    with Session() as s:
        f=s.get(ObservedFile,ref.object_id);assert f.generation>generation;fresh=reference(f,s.get(ObservationScope,f.scope_id))
    assert resolve(ref).availability=='changed'
    assert resolve(fresh).availability=='available'
    # Two observers must not assign different bytes to the same generation.
    import threading
    from unittest.mock import patch
    from services.observation.files import regular_read
    race_path=root/workspace/'race.md';race_path.write_text('initial')
    with Session.begin() as s:
        scope=s.get(ObservationScope,ref.scope_id);observe(s,scope,'race.md',scope.scan_generation)
    first_read=threading.Event();second_write=threading.Event();references={};errors=[]
    def interleaved_read(folder,path,limit=262144,**kwargs):
        result=regular_read(folder,path,limit,**kwargs)
        if path=='race.md' and threading.current_thread().name=='observer-a':
            first_read.set();assert second_write.wait(5)
        return result
    def actor(name):
        try:
            if name=='b':
                assert first_read.wait(5);race_path.write_text('second');second_write.set()
            with Session.begin() as s:
                scope=s.get(ObservationScope,ref.scope_id)
                row=observe(s,scope,'race.md',scope.scan_generation)
                references[name]=reference(row,scope,snap['epoch'])
        except Exception as exc:errors.append(exc)
    with patch('services.observation.files.regular_read',interleaved_read):
        a=threading.Thread(target=actor,args=('a',),name='observer-a');b=threading.Thread(target=actor,args=('b',),name='observer-b')
        a.start();b.start();a.join(10);b.join(10)
        assert not a.is_alive() and not b.is_alive() and not errors,errors
    assert references['b'].file_generation>references['a'].file_generation
    assert resolve(references['a']).availability=='changed'
    assert resolve(references['b']).content=='second'
    # Stale editor 409 remains authoritative, and symlink escape has no contents.
    path=workspace+'/editor.py'
    first=ok(client.put(f'/api/projects/{pid}/file',json={'path':path,'content':'one','expected_revision':0}))
    assert client.put(f'/api/projects/{pid}/file',json={'path':path,'content':'lost','expected_revision':0}).status_code==409
    assert (root/path).read_text()=='one'
    outside=root.parent/'outside-private';outside.write_text('canary-not-returned');link=root/workspace/'escape';link.symlink_to(outside)
    with Session.begin() as s:
        scope=s.get(ObservationScope,ref.scope_id);f=observe(s,scope,'escape',scope.scan_generation)
        escape=reference(f,scope)
    assert resolve(escape).availability=='unavailable'
    # Replace a scope ancestor after validation but before opening its root.
    # The observed bytes must not follow the raced link into another directory.
    from services.observation.files import scope_root as original_scope_root
    ancestor=root/Path(workspace).parts[0];held=root/'.held-scope-ancestor'
    external=root.parent/'outside-scope-directory'
    external_root=external.joinpath(*Path(workspace).parts[1:]);external_root.mkdir(parents=True)
    (external_root/'ancestor.md').write_text('outside-scope-canary')
    def swap_ancestor(scope):
        resolved=original_scope_root(scope)
        ancestor.rename(held);ancestor.symlink_to(external,target_is_directory=True)
        return resolved
    try:
        with patch('services.observation.files.scope_root',swap_ancestor),Session.begin() as s:
            scope=s.get(ObservationScope,ref.scope_id);saved=observe(s,scope,'ancestor.md',scope.scan_generation)
            assert saved.state=='unavailable' and saved.content is None
    finally:
        if ancestor.is_symlink():ancestor.unlink()
        if held.exists():held.rename(ancestor)
    # Binary previews are epoch/generation-bound, never a current-path shortcut.
    from PIL import Image
    image=root/workspace/'preview.png';Image.new('RGB',(8,9)).save(image)
    from services.observation.files import regular_read
    with Session.begin() as s:
        scope=s.get(ObservationScope,ref.scope_id);f=observe(s,scope,'preview.png',scope.scan_generation)
        image_ref=reference(f,scope)
    image_view=resolve(image_ref)
    assert image_view.availability=='available' and image_view.metadata['width']==8
    assert client.get(image_view.artifact_url).content==image.read_bytes()
    Image.new('RGB',(9,9)).save(image)
    assert client.get(image_view.artifact_url).status_code==409
    from pypdf import PdfWriter
    pdf=PdfWriter();pdf.add_blank_page(width=10,height=20);pdf.add_blank_page(width=10,height=20)
    with (root/workspace/'pages.pdf').open('wb') as stream:pdf.write(stream)
    with Session.begin() as s:
        scope=s.get(ObservationScope,ref.scope_id);f=observe(s,scope,'pages.pdf',scope.scan_generation);pdf_ref=reference(f,scope)
    pdf_ref.segment_id='pdf_page:2'
    assert resolve(pdf_ref).availability=='available' and resolve(pdf_ref).metadata['pages']==2
    pdf_ref.segment_id='pdf_page:3';assert resolve(pdf_ref).availability=='unavailable'
    large=root/workspace/'large.csv';large.write_text('value,label\n'+('1,real\n'*100000))
    data,meta=regular_read(root/workspace,'large.csv')
    assert data is None and meta['metadata']['sample_only'] and meta['metadata']['sample_bytes']<=32768
    assert meta['metadata']['columns']==['value','label'] and len(meta['metadata']['sample_rows'])<=10
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({'value':range(100000)}),root/workspace/'large.parquet')
    data,meta=regular_read(root/workspace,'large.parquet')
    assert data is None and meta['metadata']['rows']==100000 and meta['metadata']['row_groups']==1
    checkpoint=root/workspace/'model.pkl';checkpoint.write_bytes(b'not a pickle; must never deserialize'*10000)
    assert regular_read(root/workspace,'model.pkl')[1]['metadata']['coverage'].endswith('not deserialized')
    changed.unlink();assert resolve(fresh).availability=='deleted'
    # Two requests/workers: persistent coalescing and atomic claims, not a Python lock.
    with Session.begin() as s:
        provider=Provider(name='unconfigured test transport',kind='ollama',base_url='http://127.0.0.1:1',model='not-a-live-model');s.add(provider);s.flush();providerid=provider.id
    configure(pid,ReporterSettingsWrite(settings=ReporterSettings(enabled=True,provider_id=providerid,max_requests=1),expected_version=0))
    refresh_projection(pid,owner,release=True)
    with ThreadPoolExecutor(2) as pool:
        jobs=list(pool.map(lambda request:enqueue(pid,request),['one','two']))
    assert len(set(jobs))==1
    with ThreadPoolExecutor(2) as pool: claims=list(pool.map(claim,['worker-a','worker-b']))
    assert claims.count(jobs[0])==1 and claims.count(None)==1
    winner='worker-a' if claims[0] else 'worker-b'
    execute(jobs[0],winner)  # Real connection refusal; never a live-provider PASS.
    with Session() as s:
        assert s.get(ReportJob,jobs[0]).status=='failed'
        assert s.scalar(select(func.count()).select_from(TaskRun))==2
        assert s.get(PaperDocument,paperid).revision==7
        assert s.get(Project,pid).revision==0
    with Session.begin() as s:s.get(Project,pid).revision+=1
    refresh_projection(pid,owner,release=True)
    assert enqueue(pid,'two')==jobs[0]  # Coalesced request retains its original job after state changes.
    # The real guard blocks the exhausted reporting request cap before dispatch.
    capped=enqueue(pid,'request-cap');assert claim(owner)==capped
    execute(capped,owner)
    with Session() as s:
        assert s.get(ReportJob,capped).status=='budget_blocked'
        assert s.scalar(select(func.count()).select_from(ModelRequest))==1
        assert s.scalar(select(func.count()).select_from(TaskRun))==2
    # Source/project isolation and remote owner authentication.
    other=ok(client.post('/api/projects',json={'name':'Other project'}))
    assert client.post(f'/api/projects/{other["id"]}/progress/source',json=fresh.model_dump()).status_code==422
    remote=TestClient(app,base_url='https://owner.example',client=('203.0.113.8',4455))
    assert remote.get(f'/api/projects/{pid}/progress').status_code==401
    from services.api.common import owner_token
    assert remote.get(f'/api/projects/{pid}/progress',headers={'Authorization':'Bearer '+owner_token()}).status_code==200
    proxy=TestClient(app,base_url='http://localhost')
    assert proxy.get(f'/api/projects/{pid}/progress',headers={'X-Forwarded-For':'203.0.113.8'}).status_code==401
    assert proxy.get(f'/api/projects/{pid}/progress',headers={'X-Forwarded-For':'203.0.113.8','Authorization':'Bearer '+owner_token()}).status_code==200
    # A real concurrent commit cannot mix versions inside the projection transaction.
    with coherent_read() as reader:
        first=reader.scalar(select(Project.revision).where(Project.id==pid))
        def mutate():
            with Session.begin() as writer:
                write_lock(writer);project=writer.get(Project,pid);project.revision+=1
                writer.get(Node,nid).revision+=1
                writer.get(TaskRun,new.id).status='completed'
        with ThreadPoolExecutor(1) as pool: pool.submit(mutate).result(timeout=10)
        assert reader.scalar(select(Project.revision).where(Project.id==pid))==first
        assert reader.scalar(select(Node.revision).where(Node.id==nid))==0
        assert reader.scalar(select(TaskRun.status).where(TaskRun.id==new.id))=='failed'
    with Session() as s: assert s.get(Project,pid).revision==first+1
    # Injected provider outputs exercise validation and cancellation only. These
    # are not live-provider results or real billed usage.
    from unittest.mock import patch
    with patch('services.api.verification.verification_for_run',side_effect=AssertionError('Overview must not reverify')):
        view=ok(client.get(f'/api/projects/{pid}/research?overview=true'))
        assert view['trials']==[] and view['counts']['runs']==2 and 'Recorded lifecycle' in view['coverage']
    def configured():
        with Session() as s: version=s.get(ReporterPolicy,pid).version
        configure(pid,ReporterSettingsWrite(settings=ReporterSettings(enabled=True,provider_id=providerid,max_requests=5),expected_version=version))
        refresh_projection(pid,owner,release=True)
    for invalid in ['not json',json.dumps({'snapshot_id':'wrong','focus':'activity','fact_ids':[]}),json.dumps({'snapshot_id':'wrong','focus':'activity','fact_ids':[], 'prose':'completed with invented value 999'})]:
        configured();jobid=enqueue(pid,uid());assert claim(owner)==jobid
        with patch('services.observation.reporting.ModelClient.complete',return_value={'text':invalid}): execute(jobid,owner)
        with Session() as s: assert s.get(ReportJob,jobid).status=='failed'
    configured();jobid=enqueue(pid,uid());assert claim(owner)==jobid
    with Session.begin() as s:s.get(ReportJob,jobid).lease_until=time.time()-1
    assert claim('replacement-before-send') is None
    with Session() as s:assert s.get(ReportJob,jobid).status=='failed'
    configured();jobid=enqueue(pid,uid());assert claim(owner)==jobid
    from research.agents.budget import make_request_guard,BudgetExceeded
    with Session.begin() as s:
        job=s.get(ReportJob,jobid);job.status='requesting'
        version=job.policy_version;epoch=job.epoch
    guard=make_request_guard({'id':providerid,'_usage_context':{'project_id':pid,'report_job_id':jobid,'report_policy_version':version,'report_lease_owner':owner}})
    reservation=guard({'phase':'before','model':'not-a-live-model','max_output_tokens':128,'input_bytes':100})
    with Session.begin() as s:
        s.get(ReportJob,jobid).lease_until=time.time()-1
    assert claim('replacement') is None
    with Session() as s:
        assert s.get(ReportJob,jobid).status=='uncertain'
        assert s.get(ModelRequest,reservation).status=='uncertain'
    with pytest.raises(BudgetExceeded): guard({'phase':'before','model':'not-a-live-model','max_output_tokens':128,'input_bytes':100})
    configured();jobid=enqueue(pid,uid());assert claim(owner)==jobid
    def late_response(client,messages):
        context=json.loads(messages[-1]['content'])
        with Session() as s: version=s.get(ReporterPolicy,pid).version
        configure(pid,ReporterSettingsWrite(settings=ReporterSettings(enabled=False),expected_version=version))
        return {'text':json.dumps({'snapshot_id':context['snapshot_id'],'focus':'activity','fact_ids':[context['facts'][0]['id']]})}
    with patch('services.observation.reporting.ModelClient.complete',late_response): execute(jobid,owner)
    with Session() as s: assert s.get(ReportJob,jobid).status=='cancelled'
    for change in ('science','provider'):
        configured();jobid=enqueue(pid,uid());assert claim(owner)==jobid
        def changed_during_response(client,messages):
            context=json.loads(messages[-1]['content'])
            with Session.begin() as writer:
                if change=='science':
                    get(writer,Project,pid,for_update=True).revision+=1
                    writer.get(Node,nid).revision+=1
                else:writer.get(Provider,providerid).config={'changed_during_request':True}
            return {'text':json.dumps({'snapshot_id':context['snapshot_id'],'focus':'activity','fact_ids':[context['facts'][0]['id']]})}
        from services.api.common import get
        with patch('services.observation.reporting.ModelClient.complete',changed_during_response):execute(jobid,owner)
        with Session() as s:assert s.get(ReportJob,jobid).status=='superseded'
    # Cancellation cannot free a global dispatch slot while another API could
    # still be receiving the in-flight response. One uncertain earlier slot is
    # retained; this blocked call occupies the second until it actually returns.
    configured();jobid=enqueue(pid,uid());assert claim(owner)==jobid
    entered=threading.Event();finish=threading.Event()
    def blocked_response(client,messages):
        context=json.loads(messages[-1]['content']);entered.set();assert finish.wait(10)
        return {'text':json.dumps({'snapshot_id':context['snapshot_id'],'focus':'activity','fact_ids':[context['facts'][0]['id']]})}
    with patch('services.observation.reporting.ModelClient.complete',blocked_response),ThreadPoolExecutor(1) as pool:
        future=pool.submit(execute,jobid,owner)
        try:
            assert entered.wait(5)
            configured()  # Cancels the first job while its response is outstanding.
            nextjob=enqueue(pid,uid())
            assert claim('second-api') is None
        finally:finish.set()
        future.result(timeout=5)
    assert claim('second-api')==nextjob
    with patch('services.observation.reporting.ModelClient.complete',return_value={'text':'invalid'}):execute(nextjob,'second-api')
    # A stale SSH executor cannot relabel its observations as the new attempt.
    from services.observation.remote import capture
    with Session.begin() as s:
        run=s.get(TaskRun,new.id);run.config={'remote':{'hostname':'owner-host'},'execution_attempt':{'id':'current-attempt'}}
        sync_scopes(s,pid);s.flush()
        remote_scope=s.scalar(select(ObservationScope).where(ObservationScope.object_id==new.id,ObservationScope.kind=='remote_workspace'));remote_id=remote_scope.id
    capture(new.id,{'files':[{'path':'result.json','content':'{"value":999}','size':13}]},'old-attempt')
    with Session() as s:assert not list(s.scalars(select(ObservedFile).where(ObservedFile.scope_id==remote_id)))
    capture(new.id,{'files':[{'path':'result.json','content':'{"value":-1}','size':12}]},'current-attempt')
    with Session() as s:
        saved=s.scalar(select(ObservedFile).where(ObservedFile.scope_id==remote_id));assert saved.content==b'{"value":-1}' and saved.attempt_id=='current-attempt'
    # Pool contention in the real SSE handler must leave the event loop runnable.
    import asyncio
    from starlette.requests import Request
    from services.api.main import events
    from services.api.db import engine
    async def pool_contention():
        async def receive():
            await asyncio.sleep(3600)
        response=await events(pid,Request({'type':'http','headers':[]},receive=receive))
        stream=response.body_iterator
        assert 'connected' in await anext(stream)
        connections=[engine.connect() for _ in range(engine.pool.size()+engine.pool._max_overflow)]
        ticks=[]
        async def release():
            for i in range(3):
                await asyncio.sleep(.02);ticks.append(i)
            connections.pop().close()
        try:
            releaser=asyncio.create_task(release())
            await asyncio.wait_for(anext(stream),5)
            await releaser
            assert len(ticks)==3
        finally:
            for connection in connections: connection.close()
            await stream.aclose()
    asyncio.run(pool_contention())
    first_write=ok(client.post('/api/projects',json={'name':'First managed publication'}))['id']
    ok(client.put(f'/api/projects/{first_write}/file',json={'path':'first.md','content':'# First\n真实字节\n','expected_revision':0}))
    with Session() as s:
        first=s.scalar(select(ObservedFile).where(ObservedFile.project_id==first_write,ObservedFile.path=='first.md'))
        assert first is not None and first.attribution=='owner_editor' and first.content=='# First\n真实字节\n'.encode()
        first_generation=first.generation
    rejected=client.put(f'/api/projects/{first_write}/file',json={'path':'first.md','content':'rejected overwrite','expected_revision':0})
    assert rejected.status_code==409
    with Session() as s:
        saved=s.get(ObservedFile,first.id)
        assert saved.generation==first_generation and saved.content==first.content
    # An owner can edit an old workspace before the historical registrar has
    # reached it. Its actual output_path, rather than recency/basename, owns it.
    with Session.begin() as s:
        historical=TaskRun(id=uid(),project_id=first_write,request_id=uid(),status='completed',output_path='runs/old-unregistered')
        s.add(historical);s.flush();historical_id=historical.id
        s.add_all([TaskRun(project_id=first_write,request_id=uid(),status='completed',output_path=f'runs/fixture-{i}') for i in range(101)])
    ok(client.put(f'/api/projects/{first_write}/file',json={'path':'runs/old-unregistered/workspace/edit.md','content':'old workspace edited','expected_revision':0}))
    with Session() as s:
        old_scope=s.scalar(select(ObservationScope).where(ObservationScope.project_id==first_write,ObservationScope.object_id==historical_id,ObservationScope.kind=='run_workspace'))
        edited=s.scalar(select(ObservedFile).where(ObservedFile.scope_id==old_scope.id,ObservedFile.path=='edit.md'))
        assert edited.attribution=='owner_editor' and edited.content==b'old workspace edited'
    ok(client.post(f'/api/projects/{first_write}/upload',files={'file':('table.csv',b'column\n1\n','text/csv')}))
    with Session() as s:
        uploaded=s.scalar(select(ObservedFile).where(ObservedFile.project_id==first_write,ObservedFile.path=='uploads/table.csv'))
        assert uploaded.attribution=='upload';upload_generation=uploaded.generation
    from services.api.config import settings as test_settings
    original_limit=test_settings.max_upload_mb
    try:
        test_settings.max_upload_mb=0  # Inject a restrictive upload limit, not an accepted fake file.
        assert client.post(f'/api/projects/{first_write}/upload',files={'file':('table.csv',b'rejected','text/csv')}).status_code==413
    finally:test_settings.max_upload_mb=original_limit
    with Session() as s:
        retained=s.get(ObservedFile,uploaded.id)
        assert retained.generation==upload_generation and retained.content==b'column\n1\n'
    # Actual monitor and verification writers contend on the same mutable
    # resource JSON. Inject only the check computation to pause the finisher;
    # persistence and WorkerLoop.monitor run against the real SQLite/PG DB.
    from services.worker.main import WorkerLoop
    from services.api.verification import finish_verification
    from services.api.db import asdict,now
    worker=WorkerLoop();race_project=ok(client.post('/api/projects',json={'name':'Verification writer race'}))['id']
    with Session.begin() as s:
        race=TaskRun(id=uid(),project_id=race_project,request_id=uid(),kind='verification',status='running',
            worker_id=worker.id,pid=os.getpid(),started_at=now(),output_path='runs/verification-race',config={})
        s.add(race);s.flush();race_id=race.id;value=asdict(race)
    (project_dir(race_project)/value['output_path']).mkdir(parents=True,exist_ok=True)
    entered=threading.Event();allow_finish=threading.Event();monitor_started=threading.Event()
    def paused_check(session,run,execution=False):
        assert execution;entered.set();assert allow_finish.wait(10)
        return {'verification_status':'accepted','artifact_observations':[]}
    def monitor_writer():
        monitor_started.set();worker.monitor(value)
    with patch('services.api.verification._evaluate',paused_check),patch('services.worker.main.process_matches',return_value=True),patch('services.worker.main.process_tree_resources',return_value={'rss_bytes':1}),ThreadPoolExecutor(2) as pool:
        finisher=pool.submit(finish_verification,race_id)
        assert entered.wait(5)
        monitor=pool.submit(monitor_writer);assert monitor_started.wait(5)
        try:
            time.sleep(.2)
            assert not monitor.done()  # Without writer coordination it commits stale JSON here.
        finally:allow_finish.set()
        finisher.result(timeout=5);monitor.result(timeout=5)
    with Session.begin() as s:
        saved=s.get(TaskRun,race_id)
        assert saved.resource['verification_receipt']['checked_at'] and saved.resource['elapsed_seconds']>=0
        saved.status='completed';saved.pid=None
    # Normal competing observers must advance a real inventory despite an
    # unavailable branch sorting ahead of it. This exercises scheduler fairness.
    fresh_project=ok(client.post('/api/projects',json={'name':'Competing observers'}))['id']
    fresh_root=project_dir(fresh_project)
    with Session.begin() as s:
        ensure_state(s,fresh_project)
        branch=s.scalar(select(Branch).where(Branch.project_id==fresh_project));branch.workspace='branches/not-created'
        s.flush();sync_scopes(s,fresh_project);s.flush()
        missing=s.scalar(select(ObservationScope).where(ObservationScope.project_id==fresh_project,ObservationScope.kind=='branch_workspace'))
        missing.id='00000000-'+uid();missing_id=missing.id
    for i in range(301):(fresh_root/f'file-{i:04}.md').write_text(f'# Source\n{i}\n')
    from services.observation.service import ObservationService
    observers=[ObservationService(),ObservationService()]
    try:
        for observer in observers:observer.start()
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            with Session() as s:
                count=s.scalar(select(func.count()).select_from(ObservedFile).where(ObservedFile.project_id==fresh_project))
                missing=s.get(ObservationScope,missing_id)
                observed=bool(missing.observed_at and missing.coverage=='unavailable')
                if count==301 and observed:break
            time.sleep(.1)
        assert count==301 and observed
        with Session() as s:
            assert s.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.project_id==fresh_project))==0
            state=s.get(ObservationState,fresh_project);assert state.generation>0 and state.lease_owner in [observer.owner for observer in observers]
    finally:
        for observer in observers:observer.close()
    # Restore clears all derived source content and active leases, disables billing.
    from scripts.backup import quarantine_execution
    from services.api.config import settings
    quarantine_execution(settings.database_url)
    with Session() as s:
        assert s.get(ObservationState,pid) is None
        assert s.scalar(select(func.count()).select_from(ObservedFile))==0
        assert not s.get(ReporterPolicy,pid).settings['enabled']
    assert resolve(fresh).availability=='changed'
    print(json.dumps({'status':'PASS','backend':settings.database_url.split(':')[0],'cases':['empty','history','scope separation','UTF-8 source','same-stat edit','editor conflict','symlink','deletion','job coalescing','atomic claim','failure isolation','owner access','restore']}))

@pytest.mark.parametrize('backend',['sqlite','postgresql'])
def test_real_database_scenarios(tmp_path,backend):
    env={**os.environ,'FOREST_DATA_DIR':str(tmp_path/'data'),'FOREST_MODEL':'','FOREST_OWNER_TOKEN':'observation-test-owner','PYTHONPATH':str(ROOT)}
    database=None
    if backend=='postgresql':
        if not os.environ.get('FOREST_TEST_POSTGRES'): pytest.skip('Set FOREST_TEST_POSTGRES for a dedicated temporary PostgreSQL DB')
        from test_postgres import _postgres_parameters
        admin_env,flags,authority=_postgres_parameters()
        database='forest_test_observation_'+uuid.uuid4().hex
        subprocess.run(['createdb',*flags,database],env=admin_env,check=True,capture_output=True)
        env['FOREST_DATABASE_URL']='postgresql+psycopg://'+authority+'/'+database
    else: env['FOREST_DATABASE_URL']='sqlite:///'+str(tmp_path/'database.sqlite')
    try:
        r=subprocess.run([sys.executable,str(Path(__file__).resolve()),'scenario'],env=env,cwd=ROOT,capture_output=True,text=True,timeout=120)
        assert r.returncode==0,r.stdout+'\n'+r.stderr
    finally:
        if database: subprocess.run(['dropdb',*flags,'--force',database],env=admin_env,check=True,capture_output=True)

if __name__=='__main__': scenario()
