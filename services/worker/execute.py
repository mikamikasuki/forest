"""One durable task process. Parent worker owns scheduling and process control."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from services.api.db import *
from services.api.common import *
from services.api.common import touch_dependents
from services.api.config import ROOT

def local_command(command, workspace, output, config):
    """Save the actual subprocess phase independently of the whole run receipt."""
    if config.get('execution_backend')=='bubblewrap':
        return isolated_command(command,workspace,output,config)
    from research.execution.repository import task_environment
    from research.execution.repository import _write_manifest
    started = datetime.now(timezone.utc).isoformat()
    receipt = {'backend':'local','command':command,'status':'running','started_at':started}
    _write_manifest(output/'execution.json',receipt)
    env={**task_environment(config.get('env',{})),'FOREST_RUN_DIR':str(output),'PYTHONPATH':str(ROOT)}
    try:
        proc=subprocess.run(command,cwd=workspace,env=env,text=True)
    except Exception:
        receipt.update(status='failed',finished_at=datetime.now(timezone.utc).isoformat())
        _write_manifest(output/'execution.json',receipt)
        raise
    receipt.update(status='completed' if proc.returncode==0 else 'failed',exit_code=proc.returncode,
                   finished_at=datetime.now(timezone.utc).isoformat())
    _write_manifest(output/'execution.json',receipt)
    return proc


def isolated_command(command,workspace,output,config):
    """Deterministic jobs use the same durable isolated process supervisor."""
    from runners.bubblewrap import BubblewrapProcesses
    from research.agents.processes import atomic_json,read_json
    manager=BubblewrapProcesses(workspace,config)
    configuration={key:value for key,value in config.items() if key not in (
        'execution_attempt','_next_attempt','_recovery_failures','_context_workspace','provider_snapshot')}
    binding_path=output/'deterministic_jobs.json'
    bindings=read_json(binding_path,{'jobs':[]})
    previous=bindings['jobs'][-1] if bindings['jobs'] else None
    attempt=config.get('execution_attempt',{})
    if previous:
        try:original=manager.inspect(previous['work_id'])
        except ValueError:
            # A crash after accepting the work ID but before recording launch
            # has no process side effect. Reuse that same accepted identity.
            folder=manager.folder(previous['work_id'])
            if folder.exists() and any(folder.iterdir()):raise
            original={'status':'accepted'}
        new_work=(original['status'] in ('failed','cancelled') and attempt.get('mode') in ('restart','checkpoint')
                  and previous.get('attempt_id')!=attempt.get('id'))
    else:new_work=True
    if new_work:
        previous={'work_id':uid(),'attempt_id':attempt.get('id'),'command':command,'configuration':configuration}
        bindings['jobs'].append(previous)
        atomic_json(binding_path,bindings)  # Accept identity before the process side effect.
    elif previous['command']!=command or ({key:value for key,value in previous['configuration'].items() if key!='timeout'} !=
            {key:value for key,value in configuration.items() if key!='timeout'}):
        raise ValueError('Accepted deterministic job configuration changed; reconcile its original execution before new work')
    work_id=previous['work_id']
    state=manager.start(command,process_id=work_id,env=config.get('env',{}),
                        experiment=previous['configuration'])
    while state['status'] not in ('completed','failed','cancelled','lost','reconciliation'):
        time.sleep(.05);state=manager.inspect(work_id)
    atomic_json(output/'execution.json',{'backend':'bubblewrap','work_id':work_id,
        'command':command,'status':state['status'],'exit_code':state.get('exit_code'),
        'original_receipt':str(manager.folder(work_id)/'completion.json')})
    if state['status'] in ('lost','reconciliation'):
        from research.agents.runtime import AgentYield
        raise AgentYield('waiting_input',wait_for={'type':'execution_reconciliation','process_id':work_id,
                         'workspace':str(workspace)},
                         reason='Deterministic execution requires reconciliation of its original process receipt')
    for stream in ('stdout','stderr'):
        path=manager.folder(work_id)/(stream+'.log')
        if path.is_file():
            destination=sys.stdout if stream=='stdout' else sys.stderr
            destination.write(path.read_text(errors='replace'));destination.flush()
    result=subprocess.CompletedProcess(command,state.get('exit_code',1))
    result.original_artifacts=state.get('artifacts',[])
    return result


def require_command_artifact(process,path,workspace):
    """Promote only outputs retained by this actual isolated execution."""
    if not hasattr(process,'original_artifacts'):return
    from research.agents.processes import artifact_matches
    relative=path.relative_to(workspace).as_posix()
    original=next((item for item in process.original_artifacts if item.get('relative_path')==relative),None)
    if not original or not artifact_matches(path,original):
        raise ValueError('Command output is absent or differs from its original execution artifact: '+relative)


def isolated_entrypoint(entrypoint,workspace,output,config):
    """Only branch code/runtime mounts are exposed; supervisor state stays outside."""
    import re
    from research.agents.processes import atomic_json,artifact_matches,read_json
    module,function=entrypoint.split(':',1)
    if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',module) or not re.fullmatch(r'[A-Za-z_]\w*',function):
        raise ValueError('Entrypoint requires Python module:function identifiers')
    control=workspace/'entrypoint-control';control.mkdir(exist_ok=True)
    child_output=workspace/'entrypoint-output';child_output.mkdir(exist_ok=True)
    alias=child_output/'workspace'
    if not alias.exists():alias.symlink_to(workspace,target_is_directory=True)
    atomic_json(control/'request.json',{'module':module,'function':function,
        'parameters':config.get('parameters',{}),'output':str(child_output)})
    wrapper=control/'execute.py'
    wrapper.write_text('''import importlib,json,sys\nfrom pathlib import Path\nrequest=json.loads(Path(sys.argv[1]).read_text())\nsys.path.insert(0,str(Path.cwd()))\nresult=getattr(importlib.import_module(request['module']),request['function'])(request['parameters'],request['output'])\nPath(sys.argv[2]).write_text(json.dumps(result,allow_nan=False))\n''')
    result_path=control/'result.json'
    proc=isolated_command([sys.executable,str(wrapper),str(control/'request.json'),str(result_path)],workspace,output,config)
    if proc.returncode:raise RuntimeError('Experiment entrypoint exited '+str(proc.returncode))
    completion=read_json(Path(read_json(output/'execution.json')['original_receipt']))
    artifacts={item.get('relative_path'):item for item in completion.get('artifacts',[])}
    original=artifacts.get(result_path.relative_to(workspace).as_posix())
    if not original or not artifact_matches(result_path,original):
        raise ValueError('Entrypoint result does not match its original completion artifact')
    result=json.loads(result_path.read_text())
    if not isinstance(result,dict):raise ValueError('Experiment entrypoint must return an object')
    for source in child_output.rglob('*'):
        if source.is_symlink():continue
        if source.is_file():
            if not source.resolve().is_relative_to(child_output.resolve()):raise PermissionError('Entrypoint output escapes its authorized directory')
            original=artifacts.get(source.relative_to(workspace).as_posix())
            if not original or not artifact_matches(source,original):raise ValueError('Entrypoint output changed after completion')
            target=output/source.relative_to(child_output);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    return result

def relative_paths(value,root):
    if isinstance(value,dict): return {k:relative_paths(v,root) for k,v in value.items()}
    if isinstance(value,list): return [relative_paths(v,root) for v in value]
    if isinstance(value,str) and value.startswith(str(root)): return str(Path(value).relative_to(root))
    return value

def latest_experiment(s,pid,ids=None):
    from services.interventions.applicability import goal_applicability
    if ids is not None:
        if not isinstance(ids,list) or not ids or any(not isinstance(rid,str) or not rid for rid in ids) or len(ids)!=len(set(ids)):
            raise ValueError('Explicit evidence run_ids must be a nonempty list of unique run IDs')
        result=[get(s,TaskRun,rid) for rid in ids]
        if any(r.project_id!=pid for r in result): raise ValueError('Cross-project run reference')
        if any(r.status!='completed' for r in result): raise ValueError('Evidence must come from completed runs')
        if any(not goal_applicability(s, r)['ready'] for r in result):
            raise ValueError('Review selected evidence against the current goal before using it')
        return result
    for run in s.scalars(select(TaskRun).where(TaskRun.project_id==pid,
            TaskRun.kind.in_(('experiment','agent','command')),TaskRun.status=='completed').order_by(TaskRun.created_at.desc())):
        if goal_applicability(s, run)['ready']: return [run]
    return []


def statistical_run_evidence(root, selected):
    """Read every explicitly selected scientific artifact with its saved design."""
    from research.paper.evidence import collect_evidence, resolve_run_metrics_file
    return collect_evidence([{'id':run.id,'status':run.status,
        'directory':str(safe_path(root,run.output_path,True)),
        'metrics_file':resolve_run_metrics_file(safe_path(root,run.output_path,True),run.config.get('metrics_file','metrics.json')),
        'config':{key:value for key,value in run.config.items() if key not in ('provider_snapshot','env')}}
        for run in selected])


def _figure_source_runs(data):
    if not isinstance(data, dict):
        return []
    source_run_ids = data.get('source_run_ids')
    run_ids = data.get('run_ids')
    for value in (source_run_ids, run_ids):
        if value is not None and (
            not isinstance(value, list)
            or any(not isinstance(identifier, str) or not identifier.strip() for identifier in value)
            or len(set(value)) != len(value)
        ):
            return None
    if source_run_ids is not None:
        if source_run_ids and run_ids and source_run_ids != run_ids:
            return None
        return list(source_run_ids)
    return list(run_ids or [])


def manuscript_figures(session, project_id, root, selected, figure_ids=None):
    """Bridge separately rendered studio figures into scientific-run evidence."""
    allowed = {run.id for run in selected}
    rows = list(session.scalars(select(Figure).where(Figure.project_id == project_id)))
    if figure_ids is not None:
        if (not isinstance(figure_ids, list)
                or any(not isinstance(identifier, str) or not identifier.strip() for identifier in figure_ids)
                or len(set(figure_ids)) != len(figure_ids)):
            raise ValueError('figure_ids must be a list of unique project figure IDs')
        lookup = {row.id:row for row in rows}
        if set(figure_ids)-set(lookup): raise ValueError('A selected manuscript figure is unavailable in this project')
        rows = [lookup[identifier] for identifier in figure_ids]
    records = []
    for row in rows:
        data = row.data if isinstance(row.data, dict) else {}
        outputs = data.get('outputs') if isinstance(data.get('outputs'), dict) else {}
        linked = _figure_source_runs(data)
        if not linked:
            if figure_ids is not None:
                raise ValueError('Selected figure must declare source runs within the manuscript evidence scope')
            continue
        if not set(linked)<=allowed:
            if figure_ids is not None: raise ValueError('Selected figure uses runs outside the manuscript evidence scope')
            continue
        if row.status!='ready_for_review':
            if figure_ids is not None: raise ValueError('Render and review selected figures before generating the manuscript')
            continue
        path = next((outputs.get(key) for key in ('pdf','png','jpg','jpeg') if isinstance(outputs.get(key), str) and outputs.get(key)),None)
        if not path: continue
        asset = safe_path(root,path,True)
        artifacts = {}
        for key in ('source','data','style','report','selection','caption_context'):
            if isinstance(outputs.get(key), str) and outputs.get(key):
                original=safe_path(root,outputs[key],True)
                if original.parent==asset.parent: artifacts[key]=original.name
        records.append({'id':row.id,'run_id':linked[0],
            'source_run_ids':list(linked),'directory':str(asset.parent),'path':asset.name,
            'caption':data.get('caption') or row.title,'purpose':data.get('purpose',data.get('kind')),
            'anchor':data.get('anchor'),'artifacts':artifacts})
    return records


def saved_paper_response(session, project_id, config, current_run_id):
    """Resolve only a named response artifact of a terminal paper-generation run."""
    import re
    origin_id, relative = config.get('saved_response_run_id'), config.get('saved_response_path')
    if not isinstance(origin_id,str) or not isinstance(relative,str):
        raise ValueError('Saved response reuse requires saved_response_run_id and saved_response_path')
    origin=get(session,TaskRun,origin_id)
    if origin.project_id!=project_id: raise ValueError('Saved response belongs to another project')
    if origin.id==current_run_id or origin.kind!='paper_generate' or origin.status not in ('completed','failed','cancelled','interrupted'):
        raise ValueError('Reuse a terminal original paper_generate run')
    parts=Path(relative).parts
    if Path(relative).is_absolute() or len(parts)!=4 or parts[:2]!=('paper','model_responses') or not re.fullmatch(r'[A-Za-z0-9-]+',parts[2]) or not re.fullmatch(r'attempt-[1-9][0-9]*\.json',parts[3]):
        raise ValueError('Select paper/model_responses/GENERATION_ID/attempt-N.json from the originating run')
    if not isinstance(config.get('run_ids'),list) or not config['run_ids'] or config['run_ids']!=origin.config.get('run_ids'):
        raise ValueError('Saved response reuse requires the original explicit ordered evidence run_ids')
    origin_root=safe_path(project_dir(project_id),origin.output_path,True)
    lexical=origin_root/relative
    if any(path.is_symlink() for path in [lexical,*list(lexical.parents)[:3]]):
        raise ValueError('Saved response artifacts must be regular files, not symlinks')
    path=safe_path(origin_root,relative,True)
    if not path.is_file(): raise ValueError('Saved response artifact is not a file')
    generation_evidence=path.parent/'input_evidence.json'
    return origin,path,generation_evidence if generation_evidence.is_file() else origin_root/'paper'/'input_evidence.json'

def model_json(config,prompt,system='',*,output_retry_attempts=None):
    from research.agents.provider import ModelClient
    from research.agents.policy import RESEARCH_POLICY
    from research.agents.output_budget import configure_request_policy
    provider=config.get('provider_snapshot')
    if not provider: raise ValueError('Connect a model provider in Settings before running this task')
    from research.planning.model import context_model_json
    client=configure_request_policy(ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False)),config)
    return context_model_json(client,[{'role':'system','content':RESEARCH_POLICY+'\n'+system},{'role':'user','content':prompt}],
                              Path(config['_context_workspace']),char_hint=config.get('context_char_budget',64000),
                              output_stage=client.config.get('output_stage','research'), output_retry_attempts=output_retry_attempts)

def copy_task_file(origin,destination):
    """A crashed preparation may leave a temporary file, never a partial input."""
    temporary=destination.with_name(destination.name+'.preparing.'+str(time.time_ns()))
    try:
        shutil.copy2(origin,temporary)
        with temporary.open('rb') as handle: os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_branch_references(root, workspace, bindings, config=None, catalog=None):
    """Copy branch-level reference files into an isolated task workspace."""
    # Keep the nearest/last mapping when nested forks reference the same path.
    by_destination = {binding['destination']: binding for binding in bindings}
    from research.agents.path_access import copy_origins,denied_origin
    copied = {}
    for binding in by_destination.values():
        origin=safe_path(root,binding['source_path'],True)
        origins=copy_origins(root,origin,catalog)
        if denied_origin(origin,config,origins):
            raise PermissionError('Explicitly requested branch input has a denied origin: '+str(origin))
        destination=safe_path(workspace,binding['destination'])
        if destination.is_file():
            continue
        if destination.exists():
            raise ValueError('A branch reference destination is not a file path')
        destination.parent.mkdir(parents=True,exist_ok=True)
        copy_task_file(origin,destination)
        copied[Path(binding['destination']).as_posix()]=origins
    return copied


def prepare_task_workspace(root, source_workspace, output, config):
    """Resume interrupted input preparation before running any model or fit."""
    from research.agents.processes import atomic_json
    from research.agents.path_access import copy_origin_catalog,copy_origins,denied_origin
    workspace=output/'workspace'
    marker=output/'workspace_preparation.json'
    preparation=json.loads(marker.read_text()) if marker.exists() else None
    catalog=copy_origin_catalog(root)
    for binding in [*config.get('resolved_inputs',[]),*config.get('resolved_branch_references',[])]:
        origin=safe_path(root,binding['source_path'],True)
        if denied_origin(origin,config,copy_origins(root,origin,catalog)):
            raise PermissionError('Explicitly requested task input has a denied origin: '+str(origin))
    if preparation and preparation.get('status')=='completed':
        return
    if preparation is None and config.get('execution_attempt',{}).get('number',1)>1:
        # Legacy workspaces can already contain executed, edited outputs. Never
        # overwrite those with their original branch when migrating an old run.
        atomic_json(marker,{'status':'completed','legacy_existing_workspace':True})
        return
    atomic_json(marker,{'status':'preparing','source_workspace':str(source_workspace.relative_to(root)),
        'inputs':config.get('resolved_inputs',[]),
        'branch_references':config.get('resolved_branch_references',[])})
    source_origins={}
    if source_workspace!=root:
        for source in source_workspace.rglob('*'):
            if source.is_file() and not source.is_symlink():
                origins=copy_origins(root,source,catalog)
                destination=workspace/source.relative_to(source_workspace)
                if denied_origin(source,config,origins):
                    if destination.exists():
                        source_origins[source.relative_to(source_workspace).as_posix()]=origins
                    continue
                destination.parent.mkdir(parents=True,exist_ok=True)
                copy_task_file(source,destination)
                source_origins[source.relative_to(source_workspace).as_posix()]=origins
    source_origins.update(materialize_branch_references(root,workspace,config.get('resolved_branch_references',[]),config,catalog))
    references={}
    for binding in config.get('resolved_inputs',[]):
        origin=safe_path(root,binding['source_path'],True)
        destination=safe_path(workspace,binding['destination'])
        destination.parent.mkdir(parents=True,exist_ok=True)
        copy_task_file(origin,destination)
        source_origins[Path(binding['destination']).as_posix()]=copy_origins(root,origin,catalog)
    for binding in [*config.get('resolved_inputs',[]),*config.get('resolved_branch_references',[])]:
        destination=safe_path(workspace,binding['destination'])
        from research.agents.processes import file_metadata
        references[binding['destination']]={'input_record_id':uid(),
            'source_path':binding['source_path'],'path':str(destination),**file_metadata(destination)}
    atomic_json(marker,{'status':'completed','source_workspace':str(source_workspace.relative_to(root)),
        'input_references':references,'source_origins':source_origins})

def idea_source_material(source):
    metadata={key:source.data[key] for key in ('authors','year','doi','arxiv_id','url','abstract','journal','source') if key in source.data}
    return {**metadata,'title':source.title,'source_id':source.id,'trusted_instructions':False}

def idea_source_provenance(idea,source_by_id):
    if not isinstance(idea,dict): return idea
    available=list(source_by_id)
    supporting=idea.get('supporting_source_ids',[])
    background=idea.get('background_source_ids',[])
    if not isinstance(supporting,list): supporting=[]
    if not isinstance(background,list): background=[]
    supporting=list(dict.fromkeys(ident for ident in supporting if isinstance(ident,str) and ident in source_by_id))
    supporting_set=set(supporting)
    background=list(dict.fromkeys(ident for ident in background if isinstance(ident,str) and ident in source_by_id and ident not in supporting_set))
    classified=set(supporting)|set(background)
    background.extend(ident for ident in available if ident not in classified)
    idea={key:value for key,value in idea.items() if key not in ('supporting_source_ids','background_source_ids')}
    return {**idea,'source_ids':supporting,
            'source_titles':[source_by_id[ident]['title'] for ident in supporting],
            'supporting_source_ids':supporting,
            'supporting_source_titles':[source_by_id[ident]['title'] for ident in supporting],
            'background_source_ids':background,
            'background_source_titles':[source_by_id[ident]['title'] for ident in background],
            'retrieved_source_count':len(available),
            'source_reading_scope':'retrieved_metadata_and_available_abstracts'}

def path_request_graph(graph,scope,node_id,branch_id):
    if scope=='project': return graph
    nodes={item['id']:item for item in graph.get('nodes',[])}
    if scope=='node':
        if node_id not in nodes: raise ValueError('Node-scoped path request requires an existing selected node')
        from research.kernel.graph import _closure, EXECUTION
        node_ids=_closure(graph,[node_id],reverse=True,relations=EXECUTION)
    elif scope=='branch':
        node=nodes.get(node_id)
        branch_id=(node or {}).get('branch_id') or branch_id
        if not branch_id: raise ValueError('Branch-scoped path request requires a selected branch')
        node_ids={ident for ident,item in nodes.items() if item.get('branch_id')==branch_id}
    else:
        raise ValueError('Path request scope must be node, branch, or project')
    scoped={**graph}
    scoped['nodes']=[item for item in graph.get('nodes',[]) if item['id'] in node_ids]
    scoped['edges']=[edge for edge in graph.get('edges',[]) if edge.get('source') in node_ids and edge.get('target') in node_ids]
    branch_ids={item.get('branch_id') for item in scoped['nodes']}
    scoped['branches']=[item for item in graph.get('branches',[]) if item['id'] in branch_ids]
    return scoped

def execute(run_id):
    with Session() as s:
        run=get(s,TaskRun,run_id); pid=run.project_id; project=get(s,Project,pid)
        defaults=project.config.get('agent_execution',{}) if isinstance(project.config,dict) else {}
        if not isinstance(defaults,dict): raise ValueError('project.config.agent_execution must be an object')
        config={**defaults,**(run.config or {})}; kind=run.kind; node_id=run.node_id
        branch=s.get(Branch,run.branch_id) if run.branch_id else s.scalar(select(Branch).where(Branch.project_id==pid,Branch.is_main==True)); branch_workspace=branch.workspace if branch else '.'
        output=safe_path(project_dir(pid),run.output_path); root=project_dir(pid)
        source_workspace=safe_path(root,config.get('_workspace_source_snapshot',branch_workspace))
        from services.api.verification import verification_gate
        gate=verification_gate(s,run) if kind!='research_analysis' else {'ready':True}
        if not gate['ready']:
            from research.agents.runtime import AgentYield
            run.resource={**run.resource,'blocked_reason':gate['blocked_reason'],'verification_gate':gate}
            s.add(run); s.commit()
            raise AgentYield('waiting_input',reason=gate['message'])
    output.mkdir(parents=True,exist_ok=True)
    if kind=='research_analysis':
        from research.planning.workspace_analysis import execute_workspace_analysis
        return execute_workspace_analysis(run_id,config,output)
    workspace=output/'workspace'; workspace.mkdir(exist_ok=True)
    prepare_task_workspace(root,source_workspace,output,config)
    paired_input=None
    if kind!='verification':
        with Session() as s:
            live=get(s,TaskRun,run_id)
            gate=verification_gate(s,live,copied_workspace=workspace)
            if gate['ready'] and kind=='analysis' and config.get('analysis_type')=='paired':
                from services.api.verification import admitted_paired_input
                paired_input=admitted_paired_input(s,live,copied_workspace=workspace,
                    guarded=bool(gate.get('guarded')))
        if not gate['ready']:
            from research.agents.runtime import AgentYield
            with Session.begin() as s:
                live=get(s,TaskRun,run_id)
                live.resource={**live.resource,'blocked_reason':gate['blocked_reason'],'verification_gate':gate}
            raise AgentYield('waiting_input',reason=gate['message'])
    config={k:v for k,v in config.items() if k!='_repository_source'}
    repository_source=None
    if config.get('repository') is not None or kind=='repository_clone':
        from research.execution.repository import normalize_repository,prepare_repository
        if kind not in ('repository_clone','agent','command','experiment'):
            raise ValueError('Repository sources are supported for agent, command, experiment, and repository_clone tasks')
        config={**config,'repository':normalize_repository(config.get('repository'))}
        repository_source=prepare_repository(config['repository'],workspace,output)
    # Entry points run inside this interpreter. Scrub inherited host Git auth
    # references here too, after the host-only repository preparation phase.
    from research.execution.repository import task_environment
    task_environment(config.get('env',{}))  # Reject forbidden explicit overlays.
    for variable in set(os.environ)-set(task_environment()): os.environ.pop(variable,None)
    (output/'task_config.json').write_text(json.dumps({k:v for k,v in config.items() if k!='provider_snapshot'},ensure_ascii=False,indent=2))
    config={**config,'_context_workspace':str(output/'model_context')}
    if repository_source is not None: config={**config,'_repository_source':repository_source}
    def with_repository(result):
        return {**result,'repository_source':repository_source} if repository_source is not None else result
    if kind=='repository_clone': return repository_source
    if kind=='agent':
        from research.agents.runtime import run_agent
        result=run_agent(run_id,workspace,config)
        # Completed agent files are a proposed output; explicit branch copy keeps active edits safe.
        return with_repository(result)
    if kind=='verification':
        from services.api.verification import finish_verification, _contract, _artifacts
        contract=_contract(config)
        command=config.get('command')
        if command:
            # Branch snapshots or imported producer outputs cannot stand in for
            # outputs of this actual independent execution.
            reconnect=(config.get('execution_backend')=='container' and
                       config.get('execution_attempt',{}).get('mode')=='container_reconnect')
            for scope,relative in _artifacts(contract):
                if scope=='verifier' and not reconnect:
                    target=safe_path(workspace,relative)
                    if target.is_file(): target.unlink()
            if config.get('execution_backend')=='container':
                if config.get('remote'): raise ValueError('Choose container execution or remote SSH, not both')
                from runners.container import execute_container
                execute_container(config,workspace,output)
            elif config.get('remote'):
                from runners.remote import execute_remote
                outputs=[relative for scope,relative in _artifacts(contract) if scope=='verifier']
                remote={**config['remote'],'outputs':list(dict.fromkeys([*config['remote'].get('outputs',[]),*outputs]))}
                execute_remote({**config,'remote':remote},workspace,output)
            else:
                argv=['/bin/sh','-c',command] if isinstance(command,str) else command
                proc=local_command(argv,workspace,output,config)
                if proc.returncode: raise RuntimeError('Verification command exited '+str(proc.returncode))
                for scope,relative in _artifacts(contract):
                    if scope=='verifier':require_command_artifact(proc,safe_path(workspace,relative),workspace)
        return finish_verification(run_id)
    if kind=='research_route_review':
        from research.planning.route_review import execute_route_review
        return execute_route_review(run_id,config,output)
    if kind=='research_plan':
        from research.planning.loop import planning_context,compact_planning_context,apply_plan,PLANNER_INSTRUCTIONS
        context=planning_context(pid)
        context['full_context_artifact']=str((output/'planning_context.json').relative_to(root))
        (output/'planning_context.json').write_text(json.dumps(context,ensure_ascii=False,indent=2,default=str))
        context_budget=int(config.get('planner_context_chars',context['project'].get('planner_context_chars',24000)))
        view=compact_planning_context(context,context_budget)
        (output/'planning_context_view.json').write_text(json.dumps(view,ensure_ascii=False,indent=2,default=str))
        from research.planning.model import planning_model_json
        from research.agents.provider import ModelClient
        from research.agents.policy import RESEARCH_POLICY
        provider=config.get('provider_snapshot')
        if not provider: raise ValueError('Connect a model provider in Settings before planning')
        from research.agents.output_budget import configure_request_policy
        client=configure_request_policy(ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False)),config,'planning')
        repair_context=None
        for repair in range(int(config.get('structured_repair_attempts',3))):
            try:
                proposal,response=planning_model_json(client,context,output/'planning_sessions',system=RESEARCH_POLICY+'\n'+PLANNER_INSTRUCTIONS,
                                                       char_hint=context_budget,repair=repair_context)
                (output/f'research_plan_{repair+1}.json').write_text(json.dumps({'proposal':proposal,'model':response['model'],'usage':response['usage']},indent=2,ensure_ascii=False))
                result=apply_plan(pid,run_id,proposal,context['graph']['revision'])
                (output/'research_plan.json').write_text(json.dumps({'proposal':proposal,'model':response['model'],'usage':response['usage'],'result':result},indent=2,ensure_ascii=False))
                return result
            except (ValueError,KeyError,TypeError) as exc:
                (output/f'plan_error_{repair+1}.txt').write_text(str(exc))
                if repair+1>=int(config.get('structured_repair_attempts',3)): raise
                repair_context={'validation_error':str(exc)[:1000],'previous_proposal_excerpt':json.dumps(locals().get('proposal'),ensure_ascii=False,default=str)[:min(6000,context_budget//4)], 'instruction':'Correct this concrete error. Return a complete valid plan with executable graph commands. Do not claim the rejected proposal was applied.'}
    if kind in ('command','experiment') and config.get('execution_backend')=='container':
        if config.get('remote'): raise ValueError('Choose container execution or remote SSH, not both')
        from runners.container import execute_container
        return with_repository(execute_container(config,workspace,output,require_metrics=(kind=='experiment')))
    if kind in ('command','experiment') and config.get('remote'):
        from runners.remote import execute_remote
        return with_repository(execute_remote(config,workspace,output,require_metrics=(kind=='experiment')))
    if kind=='command':
        command=(config.get('recovery',{}).get('resume_command') if config.get('execution_attempt',{}).get('mode')=='checkpoint' else None) or config.get('command')
        if not command: raise ValueError('Node config requires command argv or string')
        if isinstance(command,str): command=['/bin/sh','-c',command]
        # The worker owns the task deadline and terminates this executor's
        # whole process group. subprocess.run(timeout=...) kills only its
        # direct child and can leave grandchildren after a competing receipt.
        started=time.monotonic(); proc=local_command(command,workspace,output,config)
        if proc.returncode: raise RuntimeError(f'Command exited with code {proc.returncode}')
        metrics_path=safe_path(workspace,config.get('metrics_file','metrics.json'))
        if metrics_path.is_file():require_command_artifact(proc,metrics_path,workspace)
        return with_repository({'command_exit_code':proc.returncode,'elapsed':time.monotonic()-started,**(json.loads(metrics_path.read_text()) if metrics_path.is_file() else {})})
    if kind=='experiment':
        if config.get('command'):
            command=(config.get('recovery',{}).get('resume_command') if config.get('execution_attempt',{}).get('mode')=='checkpoint' else None) or config['command']; command=['/bin/sh','-c',command] if isinstance(command,str) else command
            # Use the same worker-owned whole-task deadline as command runs.
            proc=local_command(command,workspace,output,config)
            if proc.returncode: raise RuntimeError(f'Experiment exited {proc.returncode}')
            metrics_path=safe_path(workspace,config.get('metrics_file','metrics.json'))
            if not metrics_path.is_file(): raise ValueError('Experiment completed without required metrics.json')
            require_command_artifact(proc,metrics_path,workspace)
            result=json.loads(metrics_path.read_text()); shutil.copy2(metrics_path,output/'metrics.json'); return with_repository(result)
        if config.get('entrypoint'):
            if config.get('execution_backend')=='bubblewrap':
                return with_repository(isolated_entrypoint(config['entrypoint'],workspace,output,config))
            import importlib
            module,function=config['entrypoint'].split(':',1); sys.path.insert(0,str(workspace)); result=getattr(importlib.import_module(module),function)(config.get('parameters',{}),str(output)); return with_repository(result)
        if config.get('example')!='class_weight_calibration':
            raise ValueError('Experiment requires real command or entrypoint. Implement the research task first; no default case is substituted.')
        snapshots=output/'source'
        for source_file in ('research/experiments/case.py','research/analysis/statistics.py','research/figures/render.py','research/paper/manuscript.py','requirements.lock.txt'):
            original=ROOT/source_file; copied=snapshots/source_file; copied.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(original,copied)
        from research.experiments.case import run_case,DEFAULT_CONFIG
        scientific={k:v for k,v in config.items() if k in DEFAULT_CONFIG or k=='cache_dir'}
        stage=config.get('stage','all')
        if stage=='evaluate':
            from research.experiments.case import evaluate
            source=safe_path(root,config['source_path'],True)
            # Re-evaluation is a new run; preserve the original run's outputs.
            for pattern in ('config.json','datasets.json','training.json','environment.json','design.json','data_*.csv','split_*.npz','probabilities_*.npz'):
                for artifact in source.glob(pattern):
                    if artifact.is_file(): shutil.copy2(artifact,output/artifact.name)
            result=evaluate(output,scientific or None)
        else: result=run_case(output,scientific)
        with Session.begin() as s:
            for name,meta in json.loads((output/'datasets.json').read_text()).items():
                existing=s.scalar(select(DatasetAsset).where(DatasetAsset.project_id==pid,DatasetAsset.title==name))
                if not existing: s.add(DatasetAsset(project_id=pid,title=name,data={**meta,'path':str((output/f'data_{name}.csv').relative_to(root))},status='available'))
            s.add(Analysis(project_id=pid,title='Experiment Statistics · '+run_id[:8],data={**result,'run_ids':[run_id],'path':str((output/'metrics_summary.csv').relative_to(root))},status='ready_for_review'))
            emit(s,pid,'metric_available',{'run_id':run_id})
        return result
    if kind=='analysis' and config.get('analysis_type')=='paired':
        from research.validation.statistics import paired_csv
        source=paired_input['path'] if paired_input else safe_path(root,config['path'],True)
        source_path=(paired_input['source_path'] if paired_input else
                     source.relative_to(root.resolve()).as_posix())
        safe_path(root,source_path,True)
        input_artifact=safe_path(output,'paired-input.csv')
        temporary=safe_path(output,'.paired-input-'+uid()+'.tmp')
        size_bytes=0
        try:
            with source.open('rb') as incoming, temporary.open('xb') as retained:
                while chunk:=incoming.read(1024*1024):
                    retained.write(chunk)
                    size_bytes+=len(chunk)
            temporary.replace(input_artifact)
        finally:
            temporary.unlink(missing_ok=True)
        input_artifact_path=input_artifact.relative_to(root.resolve()).as_posix()
        input_provenance={'artifact_path':input_artifact_path,'source_path':source_path,
                          'input_record_id':run_id+':paired-input','size_bytes':size_bytes}
        with input_artifact.open(newline='') as retained:
            result=paired_csv(
                input_artifact,
                unit_column=config['unit_column'],
                baseline_column=config['baseline_column'],
                candidate_column=config['candidate_column'],
                direction=config.get('direction','lower'),
                confidence=float(config.get('confidence',.95)),
                bootstrap_samples=int(config.get('bootstrap_samples',5000)),
                seed=int(config.get('seed',0)),
                meaningful_effect=float(config.get('meaningful_effect',0)),
                input_stream=retained)
        result['input_provenance']=input_provenance
        (output/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        provenance={}
        run_ids=list(config.get('run_ids',[]))
        if paired_input:
            run_ids=list(dict.fromkeys([*run_ids,paired_input['source_run_id']]))
            provenance={'source_run_id':paired_input['source_run_id'],
                'source_path':paired_input['source_path'],'checked_path':paired_input['checked_path']}
        dependency_bindings=[{
            'source_kind':'file',
            'source_id':source_path,
            'target_path':'/input_provenance/source_path',
            'artifact_path':input_artifact_path,
            'target_artifact_path':input_artifact_path,
        }]
        analysis_data={
            **result,
            'run_ids':run_ids,
            'analysis_run_id':run_id,
            'path':input_artifact_path,
            'source_path':source_path,
            'metrics_path':str((output/'metrics.json').relative_to(root)),
            'dependency_bindings':dependency_bindings,
            **({'verification_input':provenance} if provenance else {}),
        }
        from services.worker.scheduler import _lock_project
        from services.api.files import file_publication_lock
        # File publishers acquire the publication lock before the project
        # writer lock. Match that order so failed-publication compensation
        # cannot expose transient bytes between DB rollback and file restore.
        with file_publication_lock(pid,source_path):
            with Session.begin() as s:
                _lock_project(s,pid)
                try:
                    current_source=safe_path(root,source_path)
                    from research.agents.processes import file_contents_equal
                    source_matches_current=file_contents_equal(current_source,input_artifact)
                except OSError:
                    source_matches_current=False
                # Compare the retained actual input directly under both the
                # file-publication lock and project lock immediately before
                # insertion, then keep both until dependent invalidation commits.
                source_changed=not source_matches_current
                item=Analysis(project_id=pid,
                    title=config.get('title','Paired statistical analysis'),
                    data=analysis_data,status='ready_for_review')
                s.add(item)
                s.flush()
                if source_changed:
                    # A replacement may have invalidated existing dependents
                    # before this result was published. Re-run canonical
                    # invalidation while publisher lock order is still held.
                    touch_dependents(s,pid,source_path)
        return result
    if kind=='review' and config.get('review_scope')=='statistics':
        from research.validation.review import statistical_review_prompt,validate_statistical_review
        from research.planning.loop import planning_context
        result,response=model_json(config,statistical_review_prompt(planning_context(pid)))
        result=validate_statistical_review(result)
        (output/'statistical_review.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
        with Session.begin() as s:s.add(Review(project_id=pid,title='Statistical evidence review',data={**result,'run_id':run_id,'origin':'model_proposal'},status='proposed'))
        return result
    if kind=='analysis':
        from research.analysis.statistics import analyze
        with Session() as s: selected=latest_experiment(s,pid,config.get('run_ids')); paths=[safe_path(root,r.output_path,True) for r in selected]
        if not paths: raise ValueError('No completed experiment predictions are available')
        if config.get('analysis_type')=='publication' or any(isinstance(r.metrics.get('statistics'),dict) or isinstance(r.metrics.get('statistical_results'),dict) for r in selected):
            evidence=statistical_run_evidence(root,selected)
            statistics=evidence.get('statistics',{})
            if not statistics.get('recognized'):
                raise ValueError('Publication analysis requires statistical observations or saved identified results')
            if not statistics.get('coverage',{}).get('complete'):
                raise ValueError('Publication analysis has missing declared dataset/method/repetition cells; complete the experiment matrix')
            result={'analysis_type':'publication','statistical_results':statistics,'source_run_ids':[r.id for r in selected]}
            (output/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
            with Session.begin() as s:
                a=Analysis(project_id=pid,title=config.get('title','Publication statistical analysis'),
                    data={**result,'run_ids':[r.id for r in selected],'path':str((output/'metrics.json').relative_to(root))},status='ready_for_review')
                s.add(a);s.flush();emit(s,pid,'artifact_available',{'kind':'analysis','id':a.id})
            return result
        results=[]
        for i,path in enumerate(paths):
            value=analyze(path/'predictions.csv',output/f'analysis_{i}',config)
            with Session() as s:
                edited=s.scalar(select(FileRevision).where(FileRevision.project_id==pid,FileRevision.path==str((path/'predictions.csv').relative_to(root))))
            value['source_origin']=effective_file_origin(
                edited.origin if edited else None, 'executor_measurement'
            )
            if edited and edited.origin=='user_edited': value['evidence_label']='DERIVED_FROM_USER_EDITED_DATA'
            (output/f'analysis_{i}'/'metrics.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
            results.append(value)
        result=results[0] if len(results)==1 else {'analyses':results}
        with Session.begin() as s:
            a=Analysis(project_id=pid,title=config.get('title','Independent Recalculation'),data={**result,'run_ids':[r.id for r in selected],'path':str((output/'analysis_0/metrics_summary.csv').relative_to(root))},status='ready_for_review'); s.add(a); s.flush(); emit(s,pid,'artifact_available',{'kind':'analysis','id':a.id})
        return result
    if kind in ('figure','figure_revise'):
        from research.figures.render import render_figure,revise_style
        fig=config['figure']; fid=config['figure_id']
        if kind=='figure_revise':
            try: fig={**fig,'style':revise_style(config['instruction'],fig.get('style',{}))}
            except ValueError:
                changes,response=model_json(config,json.dumps({'figure':fig,'instruction':config['instruction'],'region':config.get('region')},ensure_ascii=False),'Return JSON {"style":{...},"explanation":"..."}; change only the requested plot styling, not measurements.')
                fig={**fig,'style':{**fig.get('style',{}),**changes['style']}}
        with Session() as s: selected=latest_experiment(s,pid,fig.get('run_ids') or config.get('run_ids'))
        style=dict(fig.get('style',{}))
        if fig.get('metric'): style['metric']=fig['metric']
        if fig.get('kind')=='image': data={**fig.get('data',{}),'mechanism':fig.get('image_prompt') or fig.get('caption')}
        elif fig.get('kind')=='method': data=fig.get('data',{})
        elif fig.get('data'): data=fig['data']
        elif fig.get('kind')=='calibration' and selected:
            import pandas as pd
            frames=[]
            for r in selected:
                source=safe_path(root,r.output_path,True)
                prediction_file=source/'predictions.csv'
                if not prediction_file.is_file(): prediction_file=source/'workspace'/'predictions.csv'
                frame=pd.read_csv(prediction_file)
                frame['source_run_id']=r.id
                frames.append(frame)
            data=pd.concat(frames,ignore_index=True).to_dict('records')
        elif selected:
            evidence=statistical_run_evidence(root,selected)
            if evidence.get('statistics',{}).get('recognized'):
                data={'statistical_results':evidence['statistics'],'metric_bindings':evidence['metrics'],
                      'source_run_ids':[r.id for r in selected]}
            elif len(selected)==1: data=selected[0].metrics
            else: raise ValueError('Multiple-run statistical plots require identified statistics.observations or per_seed results in their metric artifacts')
        else: raise ValueError('Choose real completed runs or explicitly imported figure data')
        target=output/'figure'; selection=None
        custom_code=fig.get('code') and fig.get('code_origin')!='renderer'
        provider=config.get('provider_snapshot')
        if fig.get('kind')=='method' and not data.get('nodes'):
            if not provider: raise ValueError('Supply method nodes/edges or connect the Method Illustrator model')
            from research.figures.model_workflow import design_method_graph
            from research.agents.provider import ModelClient
            from research.paper.evidence import _method_context, _numbers
            from research.agents.output_budget import configure_request_policy
            client=configure_request_policy(ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False)),config,'figure_design')
            data=design_method_graph(client,{'goal':config.get('project_goal'),'caption':fig.get('caption'),
                'method':data,'layout_width_in':style.get('layout_width_in',6.5),
                'source_runs':[{'id':r.id,'status':r.status,'metrics':r.metrics} for r in selected],
                'metrics':[{'id':str(r.id)+':'+pointer,'run_id':r.id,'pointer':pointer,'value':value}
                    for r in selected for pointer,value in _numbers(r.metrics)],
                'method_context':[_method_context(run.config) for run in selected],
                'narrative_mode':fig.get('narrative_mode',data.get('narrative_mode','scientific_story'))},target/'design')
        if provider:
            from research.figures.model_workflow import reviewed_render,reviewed_image,reviewed_custom_plot
            from research.agents.provider import ModelClient
            from research.agents.output_budget import configure_request_policy
            client=configure_request_policy(ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False)),config,'visual_review')
            context={'goal':config.get('project_goal'),'caption':fig.get('caption'),'purpose':fig.get('purpose'),
                'source_run_ids':[r.id for r in selected],'layout_width_in':style.get('layout_width_in',6.5),
                'source_runs':[{'id':r.id,'kind':r.kind,'status':r.status,'metrics':r.metrics} for r in selected]}
            if fig.get('kind') in ('image','method'):
                from research.paper.evidence import _numbers, _method_context
                context.update(method=data,method_context=[_method_context(r.config) for r in selected],
                    narrative_mode=fig.get('narrative_mode',data.get('narrative_mode','scientific_story') if fig.get('kind')=='image' or data.get('storyboard') else 'method_only'))
                context['metrics']=[{'id':str(r.id)+':'+pointer,'run_id':r.id,'pointer':pointer,'value':value}
                    for r in selected for pointer,value in _numbers(r.metrics)]
                if data.get('storyboard'):context['storyboard']=data['storyboard']
            if fig.get('kind')=='image':
                prompt=fig.get('image_prompt') or fig.get('caption')
                if not prompt:raise ValueError('Describe the actual scientific mechanism in image_prompt')
                existing_bundle=None
                if config.get('image_candidate_run_id'):
                    with Session() as s:origin=get(s,TaskRun,config['image_candidate_run_id'])
                    if origin.project_id!=pid or origin.kind not in ('figure','figure_revise') or origin.config.get('figure_id')!=fid or origin.status not in ('completed','failed','cancelled','interrupted'):
                        raise ValueError('Reuse image candidates from a terminal rendering run of this project figure')
                    iteration=config.get('image_candidate_iteration',1)
                    if isinstance(iteration,bool) or not isinstance(iteration,int) or iteration<1:raise ValueError('Choose an actual positive image candidate iteration')
                    manifest=safe_path(root,origin.output_path+f'/figure/iterations/{iteration}/candidate_manifest.json',True)
                    existing_bundle=json.loads(manifest.read_text())
                    if existing_bundle.get('kind')!='image' or len(existing_bundle.get('candidates',[]))<2:
                        raise ValueError('The selected run has no complete actual image candidate bundle')
                    admitted_root=manifest.parent.resolve()
                    for candidate in existing_bundle['candidates']:
                        for filename in candidate.get('outputs',{}).values():
                            path=Path(filename).resolve()
                            if not path.is_relative_to(admitted_root) or not path.is_file():raise ValueError('Retained image candidate assets must stay within their original rendering iteration')
                paths,selection=reviewed_image(client,target,prompt,context=context,
                    variants=fig.get('image_variants'),attempts=int(config.get('visual_review_attempts',3)),existing_bundle=existing_bundle)
            elif custom_code:
                paths,selection=reviewed_custom_plot(client,target,data,fig['code'],style,context=context)
            else:
                paths,selection=reviewed_render(client,target,data,style,fig.get('kind','bar'),
                    context=context,attempts=int(config.get('visual_review_attempts',3)),candidates=fig.get('candidates'))
        else:
            if fig.get('kind')=='image':raise ValueError('Connect and configure an actual image-generation provider before generating illustrations')
            paths=render_figure(target,data,style,fig.get('kind','bar'))
        if custom_code and not provider:
            for ext in ('svg','png','pdf'): (target/f'figure.{ext}').unlink(missing_ok=True)
            code=target/'custom_plot.py'; code.write_text(fig['code']); proc=subprocess.run([sys.executable,str(code)],cwd=target,env={**os.environ,'MPLBACKEND':'Agg','PYTHONPATH':str(ROOT)},timeout=90)
            if proc.returncode: raise ValueError('Custom plotting code failed; inspect stdout')
            if not any((target/f'figure.{ext}').exists() for ext in ('svg','png','pdf')): raise ValueError('Plot code must save figure.svg/png/pdf in its working directory')
            paths={k:v for k,v in paths.items() if k not in ('svg','png','pdf') or Path(v).is_file()}; paths['source']=str(code)
        rel=relative_paths(paths,root)
        if fig.get('kind')=='image' and rel.get('prompt'):
            rel['source']=rel['prompt']
        with Session.begin() as s:
            from services.worker.scheduler import _lock_project
            _lock_project(s, pid)
            f=get(s,Figure,fid)
            from services.interventions.applicability import goal_applicability
            sources_current = all(goal_applicability(s, get(s, TaskRun, source.id))['ready'] for source in selected)
            if f.revision==config['figure_revision'] and sources_current:
                reviewed=selection is not None
                f.data={**fig,'data':data,'outputs':rel,'source_run_ids':[r.id for r in selected],
                    'visual_selection':selection,'visual_review_status':'selected' if reviewed else 'awaiting_independent_reviews',
                    'code_origin':'custom' if custom_code else 'renderer',
                    'code':Path(paths['source']).read_text() if paths.get('source') and Path(paths['source']).suffix=='.py' else fig.get('code','')}
                f.revision+=1;touch_dependents(s,pid,fid)
                f.status='ready_for_review' if reviewed else 'needs_review'
            emit(s,pid,'artifact_available',{'kind':'figure','id':fid,'run_id':run_id})
        return {'outputs':rel,'figure_revision_used':config['figure_revision']}
    if kind=='paper_generate':
        from research.paper.manuscript import generate_paper,compile_paper,collect_evidence,manuscript_prompt
        from research.paper.layout import normalize_layout
        selected_layout=normalize_layout(config.get('layout'),config.get('template','article'))
        from services.api.paper_state import publish_generation
        with Session() as s: selected=latest_experiment(s,pid,config.get('run_ids'))
        if not selected: raise ValueError('A completed scientific run is required')
        for evidence_run in selected:
            if evidence_run.kind=='agent' and not any(x.get('status')=='completed' and x.get('exit_code')==0 for x in evidence_run.metrics.get('executions',[])):
                raise ValueError('Agent metric evidence requires an actual successful computation receipt: '+evidence_run.id)
        source=safe_path(root,selected[0].output_path,True); folder=output/'paper'; reuse=None
        has_saved_response='saved_response_run_id' in config or 'saved_response_path' in config
        if 'draft_revision_path' in config and not has_saved_response:
            raise ValueError('draft_revision_path requires an explicitly selected saved response')
        if has_saved_response and config.get('example'):
            raise ValueError('Saved-response reuse uses the generic evidence-bound manuscript path')
        if config.get('example')=='class_weight_calibration':
            from examples.class_weight_paper import generate_example_paper
            generated=generate_example_paper(source,folder,config.get('title'),config.get('template','article'))
        else:
            with Session() as s:
                sources=[asdict(x) for x in s.scalars(select(SourcePaper).where(SourcePaper.project_id==pid))]
                claims=[{'id':x.id,**x.data} for x in s.scalars(select(ResearchClaim).where(ResearchClaim.project_id==pid))]
                figures=manuscript_figures(s,pid,root,selected,config.get('figure_ids'))
            from research.paper.evidence import resolve_run_metrics_file
            evidence=collect_evidence(runs=[{'id':r.id,'status':r.status,'directory':str(safe_path(root,r.output_path,True)), 'metrics_file':resolve_run_metrics_file(safe_path(root,r.output_path,True),r.config.get('metrics_file','metrics.json')), 'config':{k:v for k,v in r.config.items() if k not in ('provider_snapshot','env')}} for r in selected],sources=sources,claims=claims,required_run_ids=config.get('run_ids'),figures=figures)
            if has_saved_response:
                from research.paper.model_draft import draft_from_saved_response
                with Session() as s: origin,response_path,original_evidence=saved_paper_response(s,pid,config,run_id)
                if original_evidence and Path(original_evidence).is_file():
                    saved=json.loads(Path(original_evidence).read_text())
                    statistical_figures=[figure for figure in saved.get('figures',[]) if figure.get('origin')=='statistical_presentation']
                    if statistical_figures:
                        origin_directory=safe_path(root,origin.output_path,True).resolve()
                        for figure in statistical_figures:
                            if not Path(figure['directory']).resolve().is_relative_to(origin_directory):
                                raise ValueError('Saved statistical figures must belong to the original manuscript run')
                        if saved.get('statistics')!=evidence.get('statistics'):
                            raise ValueError('Saved statistical figures use changed experiment evidence; request a fresh manuscript')
                        evidence=collect_evidence(runs=[{'id':r['id'],'status':'completed','directory':r['directory'],
                            'metrics_file':r['metrics_file'],'config':r.get('method_context',{})} for r in evidence['runs']],
                            sources=sources,claims=claims,required_run_ids=config.get('run_ids'),
                            figures=[*evidence.get('figures',[]),*statistical_figures])
                        evidence['statistical_presentation']=saved.get('statistical_presentation',{})
                revision_path=None
                if 'draft_revision_path' in config:
                    relative=config['draft_revision_path']
                    if not isinstance(relative,str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts or Path(relative).suffix.lower()!='.json':
                        raise ValueError('Draft revision must be an explicit project-relative JSON file')
                    revision_path=safe_path(root,relative,True)
                    if not revision_path.is_file(): raise ValueError('Draft revision path must identify a file')
                draft,response,reuse=draft_from_saved_response(response_path,evidence,folder,origin_run_id=origin.id,
                    origin_response_path=config['saved_response_path'],expected_type=config.get('manuscript_type'),original_evidence_path=original_evidence,
                    revision_path=revision_path,revision_project_path=config.get('draft_revision_path'),required_figure_ids=config.get('figure_ids'))
            else:
                from research.paper.model_draft import draft_with_model
                from research.agents.provider import ModelClient
                from research.agents.policy import agent_policy
                provider=config.get('provider_snapshot')
                if not provider: raise ValueError('Connect a real model provider before drafting a manuscript')
                from research.agents.output_budget import configure_request_policy
                client=configure_request_policy(ModelClient(provider,read_secret(provider.get('credential_ref')),config.get('allow_paid',False)),config,'paper')
                from research.paper.statistical_workflow import prepare_statistical_presentation
                evidence=prepare_statistical_presentation(evidence,folder/'statistical_presentation',selected_layout,client)
                required_figures=list(dict.fromkeys([*(config.get('figure_ids') or []),
                    *evidence.get('statistical_presentation',{}).get('figure_ids',[])]))
                manuscript_policy=agent_policy('Writer', {'authoring_mode':'manuscript'}, config.get('publication_profile'))
                draft,response=draft_with_model(client,evidence,config.get('instructions') or config.get('project_goal',''),folder,title=config.get('title'),attempts=int(config.get('paper_draft_attempts',3)),system=manuscript_policy,expected_type=config.get('manuscript_type','full_paper'),layout=selected_layout,publication=config.get('publication_profile'),required_figure_ids=required_figures if required_figures else config.get('figure_ids'))
                from research.paper.visual_review import review_placements
                draft,placement_review=review_placements(client,evidence,draft,folder/'visual_placement_reviews')
            generated=generate_paper(None,folder,config.get('title'),config.get('template','article'),evidence=evidence,draft=draft,layout=config.get('layout'))
        compiled=compile_paper(folder,repair_layout=True)
        if compiled['status']!='completed': raise RuntimeError(compiled.get('log','LaTeX compile failed'))
        with Session.begin() as s:
            data={'source':(folder/'paper.tex').read_text(),'bibtex':(folder/'references.bib').read_text(),'pdf_path':str((folder/'paper.pdf').relative_to(root)),'log':compiled['log'],'bindings':([{'run_id':r['id'],'path':str((Path(r['directory'])/r['metrics_file']).relative_to(root))} for r in evidence['runs']] if config.get('example')!='class_weight_calibration' else [{'run_id':selected[0].id,'path':str((source/'metrics.json').relative_to(root))}]),'numeric_bindings':generated['bindings'],'source_run_ids':[r.id for r in selected],'source_dir':str(folder.relative_to(root))}
            if reuse is not None: data['draft_reuse']=reuse
            if generated.get('dependency_bindings'):
                from research.paper.dependencies import bind_source_files
                data['draft_path']=str((folder/'draft.json').relative_to(root))
                data['dependency_bindings']=[{**binding,'target_artifact_path':data['draft_path']}
                    for binding in bind_source_files(generated['dependency_bindings'],evidence['runs'],root)]
            from research.paper.style import writing_profile
            plan=json.loads((folder/'layout_plan.json').read_text()) if (folder/'layout_plan.json').exists() else None
            data.update(template=config.get('template','article'),layout=plan.get('config') if plan else None,
                        layout_plan=plan,layout_preflight=compiled.get('preflight'),writing_profile=writing_profile())
            publication=publish_generation(s,pid,generated.get('title',config.get('title','Research manuscript')),data,config.get('paper_snapshot'),run_id)
            for fname in ('comparison','calibration','method'):
                fdir=folder/'figures'/fname
                if fdir.exists():
                    s.add(Figure(project_id=pid,title={'comparison':'Main Results Comparison','calibration':'Calibration Reliability','method':'Method Architecture'}[fname],status='ready_for_review',data={'kind':'bar' if fname=='comparison' else fname,'run_ids':[selected[0].id],'metric':'brier','style':{},'outputs':{ext:str((fdir/f'figure.{ext}').relative_to(root)) for ext in ('svg','pdf','png') if (fdir/f'figure.{ext}').exists()},'code':(fdir/'plot.py').read_text() if (fdir/'plot.py').exists() else ''}))
            emit(s,pid,'compile_finished',{'paper_id':publication['paper_id'],'run_id':run_id,**publication})
        return {'pdf_path':str((folder/'paper.pdf').relative_to(root)),'paper_source':str((folder/'paper.tex').relative_to(root)),'actual_compilation':True,**({'draft_reuse':reuse} if reuse is not None else {}),**publication}
    if kind=='paper_compile':
        from research.paper.manuscript import compile_paper
        from services.api.paper_state import locked_paper
        if config.get('source_scope')=='layout':
            from research.paper.layout import apply_layout
            from services.api.paper_state import publish_layout
            source=safe_path(root,config['compile_input_dir'],True); folder=output/'paper'
            if source!=output/'paper_inputs': raise ValueError('Layout inputs must belong to this compilation run')
            shutil.copytree(source,folder,dirs_exist_ok=True)
            plan=apply_layout(folder,config['layout'],config['template'])
            compiled=compile_paper(folder)
            if compiled['status']!='completed': raise RuntimeError(compiled.get('log','LaTeX compile failed'))
            data={**config['paper_data'],'source':(folder/'paper.tex').read_text(),
                  'bibtex':(folder/'references.bib').read_text(),
                  'pdf_path':str((folder/'paper.pdf').relative_to(root)),'source_dir':str(folder.relative_to(root)),
                  'log':compiled['log'],'errors':compiled.get('errors',[]),
                  'template':config['template'],'layout':plan['config'],'layout_plan':plan,
                  'layout_preflight':compiled.get('preflight')}
            if (folder/'bindings.json').exists(): data['numeric_bindings']=json.loads((folder/'bindings.json').read_text())
            with Session.begin() as s:
                publication=publish_layout(s,pid,data,config,run_id)
                emit(s,pid,'compile_finished',{'paper_id':publication['paper_id'],'run_id':run_id,**publication})
            return {'pdf_path':data['pdf_path'],'paper_source':str((folder/'paper.tex').relative_to(root)),
                    'actual_compilation':True,'layout_plan':plan,'layout_preflight':compiled.get('preflight'),**publication}
        if config.get('source_scope')=='workspace':
            from services.api.paper_state import publish_generation
            source=safe_path(root,config['compile_input_dir'],True); folder=output/('paper-'+uid())
            if source!=output/'paper_inputs': raise ValueError('Workspace compilation inputs must belong to this compilation run')
            shutil.copytree(source,folder,dirs_exist_ok=True)
            generated_names={'paper.pdf','paper.aux','paper.log','paper.bbl','paper.blg','paper.out','paper.synctex.gz'}
            submitted_files={str(path.relative_to(folder)):path.read_bytes() for path in folder.rglob('*') if path.is_file() and str(path.relative_to(folder)) not in generated_names}
            compiled=compile_paper(folder)
            if compiled['status']!='completed': raise RuntimeError(compiled.get('log','LaTeX compile failed'))
            if any(not (folder/name).is_file() or (folder/name).read_bytes()!=content for name,content in submitted_files.items()):
                raise ValueError('Manuscript inputs changed during compilation; compile the edited bundle again')
            data={'source':(folder/'paper.tex').read_text(),'bibtex':(folder/'references.bib').read_text() if (folder/'references.bib').exists() else '',
                  'pdf_path':str((folder/'paper.pdf').relative_to(root)),'log':compiled['log'],
                  'source_dir':str(folder.relative_to(root)),'bindings':[],
                  'source_run_ids':[config['origin_agent_run_id']], 'source_scope':'workspace',
                  'origin_source_path':config['origin_source_path'],'compile_input_files':config['compile_input_files'],
                  'layout_preflight':compiled.get('preflight')}
            with Session.begin() as s:
                publication=publish_generation(s,pid,config.get('title') or 'Agent manuscript',data,config.get('paper_snapshot'),run_id)
                emit(s,pid,'compile_finished',{'paper_id':publication['paper_id'],'run_id':run_id,**publication})
            return {'pdf_path':data['pdf_path'],'paper_source':str((folder/'paper.tex').relative_to(root)),
                    'actual_compilation':True,'source_scope':'workspace','origin_agent_run_id':config['origin_agent_run_id'],
                    'origin_source_path':config['origin_source_path'],**publication}
        folder=output/'paper'; source=root/'paper'
        if source.exists(): shutil.copytree(source,folder,dirs_exist_ok=True)
        else: folder.mkdir(exist_ok=True)
        (folder/'paper.tex').write_text(config['source']); (folder/'references.bib').write_text(config.get('bibtex','')); compiled=compile_paper(folder)
        with Session.begin() as s:
            p=locked_paper(s,config['paper_id'])
            if p.revision==config['paper_revision']:
                p.data={**p.data,'log':compiled['log'],'errors':compiled.get('errors',[]),'layout_preflight':compiled.get('preflight'),'compiled_revision':p.revision,'pdf_path':str((folder/'paper.pdf').relative_to(root)) if compiled.get('pdf_path') else None}; p.status='ready_for_review' if compiled['status']=='completed' else 'compile_failed'
            emit(s,pid,'compile_finished',{'paper_id':p.id,'status':compiled['status'],'run_id':run_id})
        if compiled['status']!='completed': raise RuntimeError(compiled['log'][-12000:])
        return {'pdf_path':str((folder/'paper.pdf').relative_to(root)),'compiled_revision':config['paper_revision']}
    if kind=='literature':
        from research.literature.sources import search, import_identifier
        query=config.get('query') or config.get('instructions') or config.get('project_goal')
        sources=search(query,config.get('source','crossref'),int(config.get('limit',5)))
        if config.get('identifiers'):
            for identifier in config['identifiers']: sources.append(import_identifier(identifier,output/'library'))
        (output/'sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2))
        with Session.begin() as s:
            ids=[]
            for item in sources:
                existing=s.scalar(select(SourcePaper).where(SourcePaper.project_id==pid,SourcePaper.title==item['title']))
                if existing: ids.append(existing.id); continue
                item=relative_paths(item,root); paper=SourcePaper(project_id=pid,title=item['title'],data=item,status='available'); s.add(paper); s.flush(); ids.append(paper.id)
                for passage in item.get('passages',[]): s.add(SourcePassage(project_id=pid,title=paper.title,data={'paper_id':paper.id,**passage},status='available'))
            emit(s,pid,'artifact_available',{'kind':'library','ids':ids})
        return {'source_ids':ids,'source_count':len(sources),'source_path':str((output/'sources.json').relative_to(root)),'evidence_label':'REPORTED'}
    if kind=='ideas':
        from research.literature.sources import search
        query=config.get('prompt') or config.get('project_goal')
        sources=search(query,'crossref',8)
        with Session.begin() as s:
            source_by_id={}
            for item in sources:
                source=s.scalar(select(SourcePaper).where(SourcePaper.project_id==pid,SourcePaper.title==item['title']))
                if not source: source=SourcePaper(project_id=pid,title=item['title'],data=item,status='available'); s.add(source); s.flush()
                source_by_id[source.id]=idea_source_material(source)
        prompt=json.dumps({'research_goal':query,'source_material_untrusted':list(source_by_id.values()),'count':min(int(config.get('count',8)),12)},ensure_ascii=False)
        ideas,response=model_json(config,prompt,'Propose distinct testable routes grounded in the supplied sources. For EACH idea, classify source IDs from source_material_untrusted into supporting_source_ids (only sources that directly support or motivate this specific route) and background_source_ids (contextual or unrelated search results; do not present as evidence for the route). Every supplied source ID must be classified in one of these two lists, and do not invent IDs. Return JSON {"ideas":[{"title":"...","claim":"...","mechanism":"...","nearest_work":"source title or explicitly state none was retrieved","supporting_source_ids":["..."],"background_source_ids":["..."],"baseline":"strong fair comparator","experiment":"...","study_design":{"population":"...","intervention":"...","comparator":"...","setting":"..."},"experiment_duty":"effectiveness|mechanism|scenario_value|alternative_explanation","metric":"...","best_estimate":"...","probability_range":[0,1],"confidence":"Low|Medium|High","why":"...","against":"...","decisive_unknown":"...","cheapest_resolution":"...","expected_cost":"...","base_case":{"most_likely_outcome":"...","current_best_estimate":"...","pre_experiment_bet":"...","probability_any_signal":[0,1],"probability_meaningful_improvement":[0,1],"probability_publishable_finding":[0,1],"biggest_reason_to_work":"...","biggest_reason_to_fail":"...","first_experiment":"..."}}]}. A direction is distinct only if its core study design differs in population, intervention, comparator, or setting. Changing the outcome subgroup or analysis of the same study is a follow-up, not a new direction; omit it or group it under the parent. Use consistent concise study_design facets so duplicate designs can be detected. Set top-level probability_range equal to base_case.probability_meaningful_improvement, not probability_any_signal or probability_publishable_finding. Probability ranges are subjective ESTIMATED values with reasons, not measured frequencies. Do not claim novelty from metadata. Never invent source findings or cite unseen full text. Prefer a concrete modest experiment over an unsupported promise.',output_retry_attempts=1)
        if not isinstance(ideas.get('ideas'),list) or not ideas['ideas']: raise ValueError('Model returned no valid research ideas')
        from research.validation.protocol import validate_idea
        ideas['ideas']=[validate_idea({**idea_source_provenance(item,source_by_id),'evidence_label':'ESTIMATED'}) for item in ideas['ideas']]
        from research.validation.protocol import distinct_idea_directions
        requested_count=min(int(config.get('count',8)),12)
        ideas['ideas'],omitted_count=distinct_idea_directions(ideas['ideas'])
        if not ideas['ideas']: raise ValueError('Model returned no distinct research study designs')
        with Session.begin() as s:
            ids=[]
            for item in ideas['ideas']:
                r=Hypothesis(project_id=pid,title=item.get('title','Candidate'),data=item,status='proposed'); s.add(r); s.flush(); ids.append(r.id)
            emit(s,pid,'artifact_available',{'kind':'ideas','ids':ids})
        return {'ideas':ideas['ideas'],'source_count':len(sources),'requested_count':requested_count,'distinct_count':len(ideas['ideas']),'omitted_count':omitted_count,'usage':response['usage']}
    if kind=='suggest_paths':
        with Session() as s:
            p=get(s,Project,pid); graph=graph_from_db(s,p)
            selected=s.get(Node,node_id) if node_id else None
            branch_id=selected.branch_id if selected else branch.id if branch else None
            scoped_graph=path_request_graph(graph,config.get('scope','node'),node_id,branch_id)
        proposal,response=model_json(config,json.dumps({'prompt':config.get('prompt'),'scope':config.get('scope','node'),'node_id':node_id,'graph':{k:v for k,v in scoped_graph.items() if k!='_history'}},ensure_ascii=False),'Return JSON {"commands":[{"operation":"add_node|fork_branch|edit_node|add_dependency","targets":[],"params":{}}],"rationale":"...","alternatives":[{"purpose":"advance|falsify|repair","experiment":"...","stop_condition":"..."}]}. These are editable proposals, not applied commands.')
        (output/'proposal.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2))
        with Session.begin() as s:
            r=Hypothesis(project_id=pid,title='Research Path Revision',data={**proposal,'node_id':node_id},status='proposed'); s.add(r); emit(s,pid,'artifact_available',{'kind':'proposal','run_id':run_id})
        return proposal
    if kind in ('paper_revise','review'):
        from research.paper.style import writing_contract,writing_profile
        from research.paper.writing import review_defensive_writing
        payload={'instruction':config.get('instruction',config.get('prompt','Check the strongest evidence and identify concrete issues')),
                 'source':config.get('source',''),'project_goal':config.get('project_goal'),
                 'literal_findings':review_defensive_writing(config.get('source',''))['edits']}
        result,response=model_json(config,json.dumps(payload,ensure_ascii=False),writing_contract('revision')+'\nReturn JSON {"summary":"...","edits":[{"original":"exact short span","replacement":"minimal replacement","reason":"concrete evidence or writing reason","start":0}],"issues":[]}. Inspect all manuscript prose, including defensive framing beyond literal matches. An offset is the exact zero-based source character index. Report substantive unresolved scientific issues separately from wording edits.')
        from research.paper.writing import validate_revision_proposal
        result=validate_revision_proposal(config.get('source',''),result)
        result['writing_profile']=writing_profile()
        (output/'revision_proposal.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        with Session.begin() as s:
            r=Review(project_id=pid,title='Revision Suggestions' if kind=='paper_revise' else 'Research Review',data={**result,'paper_id':config.get('paper_id'),'paper_revision':config.get('paper_revision'),'origin':'model_proposal'},status='proposed'); s.add(r); emit(s,pid,'artifact_available',{'kind':'review','run_id':run_id})
        return result
    raise ValueError('Unsupported task kind '+kind)

def await_committed_dispatch(run_id, attempt_id, *, timeout_seconds=10):
    """Fence orphan executors until their attempt/PID ownership is durable.

    The worker must spawn inside its admission transaction to claim the PID.
    A crash before commit must not let that unowned child start scientific work.
    Standalone execution without an attempt ID retains the CLI's existing use.
    """
    if not attempt_id:
        return True
    deadline=time.monotonic()+timeout_seconds
    while time.monotonic()<deadline:
        with Session() as session:
            run=session.get(TaskRun,run_id)
            if run is None or run.status in ('cancelled','completed','failed','skipped','interrupted'):
                return False
            attempt=(run.config or {}).get('execution_attempt') or {}
            if attempt.get('id')==attempt_id and run.pid==os.getpid() and run.status=='running':
                return True
        time.sleep(0.05)
    return False


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--run-id',required=True); args=parser.parse_args()
    attempt_id=os.environ.get('FOREST_ATTEMPT_ID')
    if not await_committed_dispatch(args.run_id,attempt_id):
        # Do not overwrite a later attempt's receipt or touch its workspace.
        sys.exit(75)
    start=time.monotonic()
    with Session() as s: run=get(s,TaskRun,args.run_id); out=safe_path(project_dir(run.project_id),run.output_path)
    out.mkdir(parents=True,exist_ok=True)
    try: result=execute(args.run_id); receipt={'status':'completed','exit_code':0,'metrics':result,'elapsed_seconds':time.monotonic()-start}
    except __import__('research.agents.runtime',fromlist=['AgentYield']).AgentYield as exc:
        receipt={'status':exc.status,'wait_for':exc.wait_for,'resume_after':exc.resume_after,'reason':exc.reason,
                 'error':exc.reason,'exit_code':0,'elapsed_seconds':time.monotonic()-start}
    except __import__('research.agents.budget',fromlist=['BudgetExceeded']).BudgetExceeded as exc:
        receipt={'status':'budget_exhausted','reason':str(exc),'error':str(exc),'exit_code':0,'elapsed_seconds':time.monotonic()-start}
    except Exception as exc:
        traceback.print_exc(); receipt={'status':'failed','exit_code':1,'error':str(exc),'elapsed_seconds':time.monotonic()-start}
    receipt['attempt_id']=attempt_id
    from research.agents.processes import atomic_json
    atomic_json(out/'result.json',json.loads(json.dumps(receipt,default=str)))
    sys.exit(receipt['exit_code'])
if __name__=='__main__': main()
