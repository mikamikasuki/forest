from __future__ import annotations
import asyncio
import datetime as dt
import json
import os
import pty
import select as io_select
import shutil
import signal
import subprocess
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlparse
import httpx
import psutil
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, update, func, text as sql_text
from .common import *
from .schemas import *
from .files import router as files_router
from services.worker.scheduler import enqueue,enqueue_nodes,enqueue_selected,ACTIVE,TERMINAL,requested_task_timeout,reserve_project_time,finalize_cancelled_run_elapsed,_elapsed_seconds_at
from research.agents.policy import ROLES,TOOLS
from research.agents.defaults import default_config, upgrade_default_tools, explicit_agent_config
from research.agents.budget import interrupt_run_reservations
from research.kernel import GraphError

@asynccontextmanager
async def lifespan(app):
    migrate()
    with Session.begin() as s:
        if not s.scalar(select(Host)): s.add(Host(name='Local Machine',kind='local',status='connected',config={'platform':os.uname().sysname,'isolation':'trusted_local_process'}))
        if settings.model and not s.scalar(select(Provider)):
            s.add(Provider(name='Local Ollama Model',kind='ollama',base_url=settings.ollama_url,model=settings.model,status='untested'))
        if not s.scalar(select(Agent)):
            for role,instructions in ROLES.items(): s.add(Agent(name=role,role=role,instructions=instructions,tools=list(TOOLS),config=default_config(role)))
        else:
            existing=list(s.scalars(select(Agent)))
            for agent in existing: upgrade_default_tools(agent)
            roles={agent.role for agent in existing}
            for role,instructions in ROLES.items():
                if role not in roles:
                    s.add(Agent(name=role,role=role,instructions=instructions,tools=list(TOOLS),config=default_config(role)))
    from services.observation.service import ObservationService
    observer=ObservationService(); observer.start()
    try:
        yield
    finally:
        try:
            observer.close()
        finally:
            from .terminal import close_all
            await close_all()
app=FastAPI(title='FOREST Research API',version='0.1.0',lifespan=lifespan)
TRUSTED_HOSTS={'localhost','127.0.0.1','::1','testserver'}
def authorized(request):
    token=owner_token()
    return request.cookies.get('forest_owner')==token or request.headers.get('authorization')=='Bearer '+token
@app.middleware('http')
async def security(request,call_next):
    route=request.url.path
    public=route in ('/api/health','/api/auth/login','/api/auth/status') or route.startswith('/api/shares/')
    hostname=request.url.hostname
    origin=request.headers.get('origin')
    if route.startswith('/api') and not public:
        if hostname not in TRUSTED_HOSTS and not authorized(request): return JSONResponse({'detail':{'code':'UNAUTHORIZED','message':'Owner authentication required','retryable':False}},401)
        if origin and (urlparse(origin).hostname not in TRUSTED_HOSTS) and urlparse(origin).hostname!=hostname: return JSONResponse({'detail':{'code':'ORIGIN_DENIED','message':'Cross-origin write/read denied','retryable':False}},403)
        forwarded=any(request.headers.get(name) for name in ('forwarded','x-forwarded-for','x-forwarded-host','x-forwarded-proto'))
        local=request.client and request.client.host in ('127.0.0.1','::1','testclient') and hostname in TRUSTED_HOSTS and not forwarded
        if not authorized(request) and not local: return JSONResponse({'detail':{'code':'UNAUTHORIZED','message':'Owner authentication required','retryable':False}},401)
        response=await call_next(request)
        if local and not authorized(request): response.set_cookie('forest_owner',owner_token(),httponly=True,samesite='strict',max_age=86400*14)
    else: response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'; response.headers['Referrer-Policy']='same-origin'
    return response
@app.exception_handler(GraphError)
async def graph_error(request,exc):
    return JSONResponse({'detail':{**exc.as_dict(),'suggestion':'Reload current graph state and reapply the edit.' if exc.code=='revision_conflict' else ''}},exc.status_code)
@app.post('/api/auth/login')
def auth_login(request:Request,body:dict=Body(...)):
    from fastapi.responses import JSONResponse
    if not secrets.compare_digest(str(body.get('token','')),owner_token()): error('INVALID_TOKEN','Owner token is incorrect',401)
    origin=request.headers.get('origin')
    if origin and urlparse(origin).hostname!=request.url.hostname: error('ORIGIN_DENIED','Use the same origin to sign in',403)
    response=JSONResponse({'authenticated':True}); response.set_cookie('forest_owner',owner_token(),httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=86400*14); return response
@app.get('/api/auth/status')
def auth_status(request:Request): return {'authenticated':authorized(request)}
@app.exception_handler(Exception)
async def unexpected(request,exc):
    from research.kernel import GraphError
    if isinstance(exc,GraphError): return JSONResponse({'detail':{'code':exc.code,'message':exc.message,'retryable':False,'details':getattr(exc,'detail',{})}},getattr(exc,'status_code',400))
    return JSONResponse({'detail':{'code':'INTERNAL_ERROR','message':str(exc)[:1500],'retryable':False,'suggestion':'Inspect the server output and retry after correcting the cause.'}},500)
@app.get('/api/health')
def health():
    with Session() as s: s.execute(select(1))
    return {'status':'ok','database':engine.dialect.name,'version':'0.1.0'}
@app.get('/api/projects')
def projects(archived:bool|None=None,limit:int=100,offset:int=0):
    with Session() as s:
        q=select(Project).order_by(Project.updated_at.desc()).limit(min(limit,200)).offset(max(offset,0))
        if archived is not None: q=q.where(Project.archived==archived)
        return [asdict(p) for p in s.scalars(q)]
@app.post('/api/projects')
def create_project(body:ProjectCreate):
    with Session.begin() as s:
        p=make_project(s,**body.model_dump()); return asdict(p)
@app.get('/api/projects/{ident}')
def project(ident:str):
    with Session() as s:
        p=asdict(get(s,Project,ident)); p.pop('graph_meta',None); return p
@app.patch('/api/projects/{ident}')
def edit_project(ident:str,body:dict=Body(...)):
    if 'budget' in body: validate_project_budget(body['budget'])
    from research.agents.tool_policy import validate_permissions
    if 'config' in body:
        try: validate_permissions(body['config'])
        except ValueError as exc: error('INVALID_TOOL_POLICY', str(exc), 422)
    if 'mode' in body and body['mode'] not in ('auto','assisted','manual'):
        error('INVALID_MODE','Project mode must be auto, assisted, or manual',422)
    with Session.begin() as s:
        from services.worker.scheduler import _lock_project
        p=_lock_project(s,ident)
        if body.get('request_id'):
            from services.interventions.models import Intervention
            from services.interventions.application import readback
            old=s.scalar(select(Intervention).where(Intervention.project_id==ident,Intervention.request_id==body['request_id']))
            if old:
                intent={key:value for key,value in body.items() if key not in ('request_id','expected_revision')}
                if old.kind!='configuration' or old.intent!=intent: error('REQUEST_ID_CONFLICT','Request identity belongs to different intent',409)
                return {**asdict(p),'intervention':readback(s,old)}
        if body.get('expected_revision',p.revision)!=p.revision: error('REVISION_CONFLICT','Project changed',409)
        observed=p.revision
        from services.research_evolution.service import current_goal
        current_goal(s,p)
        for k in ('name','description','goal','current_direction','archived','mode','budget','config'):
            if k in body:
                value=body[k]
                if k=='config' and p.config.get('provider_selection_required') and 'provider_id' in value:
                    value={**value}; value.pop('provider_selection_required',None)
                setattr(p,k,value)
        controller=p.config.get('controller')
        if 'mode' in body and isinstance(controller,dict) and 'autonomous' not in controller:
            p.config={**p.config,'controller':{**controller,'autonomous':p.mode=='auto'}}
        if 'budget' in body:
            from services.worker.scheduler import reserve_project_time
            for run in s.scalars(select(TaskRun).where(TaskRun.project_id==ident,TaskRun.status.in_(('running','queued','waiting','pausing'))).with_for_update()):
                if 'requested_task_timeout_seconds' not in run.resource.get('time_budget',{}):
                    requested,_=requested_task_timeout(s,run)
                    run.resource={**run.resource,'time_budget':{**run.resource.get('time_budget',{}),
                        'requested_task_timeout_seconds':requested}}
                reserved,_=reserve_project_time(s,p,run)
                if not reserved:
                    run.config={**run.config,'timeout':run.resource.get('elapsed_seconds',0)}
                emit(s,ident,'run_changed',{'run_id':run.id,'status':run.status,'budget_policy_updated':True})
        if 'goal' in body:
            for n in s.scalars(select(Node).where(Node.project_id==ident)): n.context_overrides={**n.context_overrides,'needs_refresh':True}
        p.revision+=1; emit(s,ident,'project_changed',{'revision':p.revision}); s.flush()
        from services.interventions.configuration import record_change
        receipt=record_change(s,p,observed,{key:value for key,value in body.items() if key not in ('request_id','expected_revision')},body.get('request_id'))
        return {**asdict(p),**({'intervention':receipt} if receipt else {})}
@app.delete('/api/projects/{ident}')
async def delete_project(ident:str):
    from starlette.concurrency import run_in_threadpool
    from .terminal import close_project, reopen_project
    try:
        await close_project(ident)
        return await run_in_threadpool(_delete_project_sync, ident)
    finally:
        reopen_project(ident)

def _delete_project_sync(ident:str):
    from runners.local import stop_group
    with Session.begin() as s:
        if s.bind.dialect.name=='sqlite': s.execute(sql_text('BEGIN IMMEDIATE'))
        p=s.scalar(select(Project).where(Project.id==ident).with_for_update())
        if not p: error('NOT_FOUND','Project missing',404)
        for run in s.scalars(select(TaskRun).where(TaskRun.project_id==ident)):
            if run.status not in ACTIVE:
                interrupt_run_reservations(run.id,session=s)
                continue
            from research.execution.process_manager import process_manager
            process_manager(safe_path(project_dir(run.project_id),run.output_path+'/workspace'),run.config).cancel_all()
            if run.config.get('execution_backend')=='container':
                from runners.container import cancel_container
                cancel_container(run.config,safe_path(project_dir(run.project_id),run.output_path))
            if run.config.get('remote'):
                from runners.remote import cancel_remote
                cancel_remote(run.config,safe_path(project_dir(run.project_id),run.output_path))
            if run.pid: stop_group(run.pid,run.process_created)
            interrupt_run_reservations(run.id,session=s)
        s.execute(update(ModelRequest).where(ModelRequest.project_id==ident,ModelRequest.status=='reserved').values(status='uncertain'))
        s.delete(p)
    shutil.rmtree(project_dir(ident),ignore_errors=True); return {'deleted':ident}
@app.post('/api/projects/{ident}/duplicate')
def duplicate_project(ident:str):
    import copy
    @contextmanager
    def duplicate_transaction():
        root = None
        body_failed = False

        def remember_project(project_id):
            nonlocal root
            root = project_dir(project_id)

        try:
            with Session.begin() as session:
                try:
                    yield session, remember_project
                except BaseException:
                    # An exception in the transaction body guarantees rollback.
                    body_failed = True
                    raise
        except BaseException:
            # A commit error can have an ambiguous outcome; preserve the
            # workspace unless the transaction body itself failed and rolled back.
            if body_failed and root is not None:
                try:
                    shutil.rmtree(root)
                except FileNotFoundError:
                    pass
            raise

    with duplicate_transaction() as (s, remember_project):
        from services.worker.scheduler import _lock_project
        p=_lock_project(s,ident)
        graph=copy.deepcopy(graph_from_db(s,p)); graph.pop('_history',None)
        resources={key:[asdict(r) for r in s.scalars(select(model).where(model.project_id==p.id))] for key,model in RESOURCE_MODELS.items()}
        papers=[asdict(r) for r in s.scalars(select(PaperDocument).where(PaperDocument.project_id==p.id))]
        runs=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.project_id==p.id))]
        from services.interventions.history import export_history,history_ids,import_history,historical_resource
        history=export_history(s,p.id)
        from services.research_evolution.history import export_evolution,evolution_ids,import_evolution
        from services.interventions.origin_history import export_origins,import_origins,relocate_permission_config
        evolution=export_evolution(s,p.id)
        origins=export_origins(project_dir(p.id))
        mapping={item['id']:uid() for key in ('nodes','edges','branches') for item in graph[key]}
        for rows in list(resources.values())+[papers,runs]:
            for item in rows: mapping.setdefault(item['id'],uid())
        for item in history_ids(history):mapping.setdefault(item,uid())
        for item in evolution_ids(evolution):mapping.setdefault(item,uid())
        mode=p.mode if p.mode in ('auto','assisted','manual') else 'assisted'
        duplicate_id=uid()
        remember_project(duplicate_id)
        new=make_project(s,p.name+' · Copy',p.goal,p.description,id=duplicate_id,mode=mode,budget=copy.deepcopy(p.budget),config=copy.deepcopy(p.config))
        def remap(value):
            return remap_identifiers(value,mapping,project_id=p.id,new_project_id=new.id)
        new.config=remap(new.config)
        import_evolution(s,new.id,evolution,remap)
        from services.interventions.history import historical_branches
        graph=historical_branches(remap(graph)); graph['revision']=0
        for node in graph['nodes']:
            if node.get('execution_status') in ACTIVE:
                node['copied_execution_status']=node['execution_status']; node['execution_status']='interrupted'
        if new.config.get('controller'):
            controller=remap(new.config['controller'])
            new.config={**new.config,'controller':{**controller,'status':'paused','phase':'PLAN'}}
        source=project_dir(p.id); destination=project_dir(new.id)
        new.config=relocate_permission_config(p.config,new.config,destination,str(source.resolve()),remap)
        node_configs={mapping[row['id']]:row.get('config',{}) for row in graph_from_db(s,p)['nodes']}
        for node in graph['nodes']:
            node['config']=relocate_permission_config(node_configs[node['id']],node['config'],destination,str(source.resolve()),remap)
        import_origins(destination,origins,remap,str(source.resolve()))
        for path in source.rglob('*'):
            if path.is_symlink() or '.forest-interventions' in path.relative_to(source).parts: continue
            relative=path.relative_to(source).as_posix()
            target=safe_path(destination,remap(relative))
            if path.is_dir(): target.mkdir(parents=True,exist_ok=True)
            elif path.is_file(): target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        save_graph(s,new,graph,historical=True)
        for key,rows in resources.items():
            model=RESOURCE_MODELS[key]
            for row in rows: s.add(model(id=mapping[row['id']],project_id=new.id,title=row['title'],status=row['status'],data=remap(row['data'])))
        for row in papers: s.add(PaperDocument(id=mapping[row['id']],project_id=new.id,title=row['title'],status=row['status'],data=remap(row['data'])))
        for row in runs:
            run_id=mapping[row['id']]
            s.add(TaskRun(id=run_id,project_id=new.id,node_id=remap(row.get('node_id')),branch_id=remap(row.get('branch_id')),
                request_id='copy:'+run_id,kind=row['kind'],status=row['status'] if row['status'] in TERMINAL else 'interrupted',
                config={**remap(row['config']),'origin':'copied_run','original_run_id':row['id']},node_revision=row['node_revision'],
                created_at=row['created_at'],started_at=row.get('started_at'),finished_at=row.get('finished_at'),
                output_path=remap(row['output_path']),dependencies=remap(row.get('dependencies',[])),metrics=remap(row['metrics']),resource=historical_resource(row['resource']),exit_code=row['exit_code']))
        import_history(s,new.id,history,remap)
        s.flush(); return asdict(new)
@app.get('/api/projects/{ident}/graph')
def graph(ident:str):
    with Session() as s: g=graph_from_db(s,get(s,Project,ident)); g.pop('_history',None); return g
@app.post('/api/projects/{ident}/graph/preview')
def preview(ident:str,body:GraphCommand):
    from research.kernel import GraphCommandService
    with Session() as s:
        if body.project_id and body.project_id!=ident: error('CROSS_PROJECT','The command belongs to a different project',422)
        p=get(s,Project,ident); cmd=body.model_dump(); cmd['project_id']=ident
        return GraphCommandService(graph_from_db(s,p),project_dir(ident)).preview(cmd)
@app.post('/api/projects/{ident}/graph/commands')
def command(ident:str,body:GraphCommand):
    from services.interventions.application import apply_commands
    if body.project_id and body.project_id!=ident: error('CROSS_PROJECT','The command belongs to a different project',422)
    return apply_commands(ident,body.request_id,body.expected_revision,[body.model_dump()])
@app.get('/api/nodes/{ident}')
def node(ident:str):
    with Session() as s: return asdict(get(s,Node,ident))
@app.patch('/api/nodes/{ident}')
def edit_node(ident:str,body:dict=Body(...)):
    with Session() as s: n=get(s,Node,ident); p=get(s,Project,n.project_id); pid=p.id; rev=p.revision
    body=dict(body); expected=body.pop('expected_revision',rev)
    return command(pid,GraphCommand(request_id=uid(),expected_revision=expected,operation='edit_node',targets=[ident],params=body))
@app.get('/api/nodes/{ident}/context')
def context(ident:str):
    from research.kernel import ContextBuilder
    with Session() as s:
        n=get(s,Node,ident); p=get(s,Project,n.project_id); g=graph_from_db(s,p); g['goal']=p.goal; g['budget']=p.budget
        from services.api.common import attach_context_ideas
        attach_context_ideas(s,p,g,n.context_overrides)
        role=n.config.get('role','Researcher')
        agent=s.get(Agent,n.config['agent_id']) if n.config.get('agent_id') else s.scalar(select(Agent).where(Agent.role==role))
        from research.agents.tool_policy import effective_tools
        packet=ContextBuilder(g,project_dir(p.id)).build(ident,role,n.context_overrides)
        packet['controls']['allowed_tools']=effective_tools(agent.tools if agent else None,project=p.config,node=n.config)
        if agent and not agent.enabled:packet['controls']['allowed_tools']=[]
        return packet
@app.post('/api/nodes/{ident}/context/rebuild')
def context_rebuild(ident:str,body:dict=Body(default={})):
    from services.worker.scheduler import _lock_project
    with Session() as s: project_id=get(s,Node,ident).project_id
    with Session.begin() as s:
        p=_lock_project(s,project_id); n=get(s,Node,ident,for_update=True)
        if body.get('expected_revision',p.revision)!=p.revision: error('REVISION_CONFLICT','Project changed',409)
        n.context_overrides={**n.context_overrides,**{key:value for key,value in body.items() if key!='expected_revision'},'needs_refresh':False}
        p.revision+=1; emit(s,n.project_id,'context_changed',{'node_id':ident,'revision':p.revision})
    return context(ident)
@app.post('/api/nodes/{ident}/run')
def run_node(ident:str,body:RunRequest):
    with Session.begin() as s:
        runs=enqueue_nodes(s,ident,body.scope,body.request_id,body.config); return asdict(runs[0]) if len(runs)==1 else {'runs':[asdict(r) for r in runs],'run_ids':[r.id for r in runs]}
@app.post('/api/branches/{ident}/run')
def run_branch(ident:str,body:RunRequest):
    with Session.begin() as s:
        b=get(s,Branch,ident); nodes=list(s.scalars(select(Node).where(Node.branch_id==ident,Node.archived==False)))
        if not nodes: error('EMPTY_BRANCH','Add a node first')
        return {'runs':[asdict(r) for r in enqueue_nodes(s,b.root_node_id or nodes[0].id,'descendants',body.request_id,body.config)]}
@app.get('/api/branches/compare')
def compare_branches(left:str,right:str):
    from research.kernel import BranchWorkspace
    with Session() as s:
        a=get(s,Branch,left); b=get(s,Branch,right)
        if a.project_id!=b.project_id: error('CROSS_PROJECT','Branches must belong to the same project')
        p=get(s,Project,a.project_id)
        comparison=BranchWorkspace(project_dir(a.project_id),graph_from_db(s,p)).compare(left,right)
        runs=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.branch_id.in_([left,right])))]
        return {**comparison,'left':asdict(a),'right':asdict(b),'runs':runs}
@app.get('/api/projects/{ident}/runs')
def runs(ident:str,limit:int=100,offset:int=0,include_manuscript_evidence:bool=False):
    with Session() as s:
        get(s,Project,ident)
        records=list(s.scalars(select(TaskRun).where(TaskRun.project_id==ident).order_by(TaskRun.created_at.desc()).limit(min(limit,500)).offset(offset)))
        result=[asdict(r) for r in records]
        if include_manuscript_evidence:
            from research.paper.evidence import manuscript_metrics_eligibility
            for run, record in zip(records, result):
                eligibility={'ready':False,'reason':'Run must be a completed experiment, command, or agent'}
                if run.status=='completed' and run.kind in ('experiment','command','agent'):
                    try:
                        directory=safe_path(project_dir(ident),run.output_path)
                        eligibility=manuscript_metrics_eligibility(directory,run.config.get('metrics_file','metrics.json'),run_id=run.id)
                    except (OSError,ValueError) as exc:
                        eligibility={'ready':False,'reason':str(exc)}
                record['manuscript_evidence']=eligibility
        return result
@app.get('/api/runs/{ident}')
def run(ident:str):
    with Session() as s:
        r=asdict(get(s,TaskRun,ident)); r['config']={k:v for k,v in r['config'].items() if k!='provider_snapshot'}
        r['tools']=[asdict(t) for t in s.scalars(select(ToolExecution).where(ToolExecution.run_id==ident).order_by(ToolExecution.created_at))]; return r
@app.post('/api/runs/{ident}/{action}')
def run_action(ident:str,action:str,body:dict=Body(default={})):
    if action=='cancel':
        from services.interventions.application import cancel_run
        return cancel_run(ident,body)
    if action in ('pause','resume'):
        from services.interventions.lifecycle import control_run
        return control_run(ident,action,body)
    with Session.begin() as s:
        with Session() as reader: project_id=get(reader,TaskRun,ident).project_id
        from services.worker.scheduler import _lock_project
        _lock_project(s,project_id)
        r=get(s,TaskRun,ident,for_update=True)
        if action=='retry':
            from services.worker.scheduler import _lock_project
            _lock_project(s,r.project_id)
            r=s.scalar(select(TaskRun).where(TaskRun.id==ident).with_for_update().execution_options(populate_existing=True))
            if r.status not in TERMINAL: error('RUN_ACTIVE','Only stopped runs can be restarted',409)
            requested_timeout,_=requested_task_timeout(s,r)
            return asdict(enqueue(s,r.project_id,r.kind,r.config,body.get('request_id'),s.get(Node,r.node_id) if r.node_id else None,r.dependencies,
                                  requested_timeout_override=requested_timeout,
                                  node_revision=r.node_revision if r.node_id else None))
        elif action=='skip':
            if r.status!='queued': error('INVALID_RUN_STATE','Only queued steps may be skipped',409)
            r.status='skipped'; r.finished_at=now()
        elif action=='priority': r.priority=int(body.get('priority',0))
        else: error('UNKNOWN_ACTION','Unknown run action',404)
        n=s.get(Node,r.node_id) if r.node_id else None
        if n and n.extra.get('latest_run_id')==r.id: n.execution_status=r.status
        emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status}); return asdict(r)
@app.get('/api/runs/{ident}/output')
def output(ident:str,offset:int=0,limit:int=100000,search:str=''):
    with Session() as s: r=get(s,TaskRun,ident); p=safe_path(project_dir(r.project_id),r.output_path+'/stdout.txt'); status=r.status
    start=max(offset,0); text=''; end=start
    if p.exists():
        size=min(limit,1000000)
        with p.open('rb') as f:
            f.seek(start); data=f.read(size); prefix=utf8_page_prefix(data)
            if data and not prefix:
                # A page limit smaller than the next character must not stall the cursor: read up
                # to three more bytes so the leading character completes (characters are <=4 bytes).
                f.seek(start); data=f.read(size+3); prefix=utf8_page_prefix(data)
            end=start+prefix; text=data[:prefix].decode(errors='replace')
    if search: text='\n'.join(l for l in text.splitlines() if search.lower() in l.lower())
    return {'text':text,'offset':end,'status':status}
@app.get('/api/projects/{ident}/events')
async def events(ident:str,request:Request):
    def check_project():
        with Session() as s: get(s,Project,ident)
    await asyncio.to_thread(check_project)
    def read_batch(last,first_poll):
        with Session() as s:
            if first_poll:
                oldest,latest=s.execute(select(func.min(Event.sequence),func.max(Event.sequence)).where(Event.project_id==ident)).one()
                latest=latest or 0
                if last>latest:
                    return [],{'requested_after_sequence':last,'oldest_available_sequence':oldest or 0,
                               'latest_available_sequence':latest,'resume_after_sequence':latest}
            rows=list(s.scalars(select(Event).where(Event.project_id==ident,Event.sequence>last).order_by(Event.sequence).limit(100)))
            gap=None
            if rows and rows[0].sequence>last+1:
                first=rows[0].sequence
                latest=s.scalar(select(func.max(Event.sequence)).where(Event.project_id==ident)) or rows[-1].sequence
                latest=max(latest,rows[-1].sequence)
                gap={'requested_after_sequence':last,'oldest_available_sequence':first,
                     'latest_available_sequence':latest,'resume_after_sequence':latest}
            return [(e.sequence,e.type,e.data) for e in rows],gap
    async def stream():
        cursor=request.headers.get('last-event-id',''); last=0
        if cursor:
            try: last=max(int(cursor),0)
            except ValueError: pass
        yield 'event: connected\ndata: {}\n\n'
        first_poll=True
        while not await request.is_disconnected():
            # Pool waits and database I/O must never block the ASGI event loop.
            rows,gap=await asyncio.to_thread(read_batch,last,first_poll)
            first_poll=False
            if gap:
                last=gap['resume_after_sequence']
                yield f'id: {last}\nevent: cursor_reset\ndata: {json.dumps(gap)}\n\n'
                continue
            for sequence,event_type,data in rows:
                last=sequence; yield f'id: {last}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n'
            yield ': heartbeat\n\n'; await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
_provider_health_cache={}
def provider_health(provider):
    """Short-lived transport/model availability, distinct from a prior chat test."""
    key=(provider.id,provider.updated_at)
    cached=_provider_health_cache.get(key)
    if cached and time.monotonic()-cached[0]<15: return cached[1]
    result={'id':provider.id,'available':False,'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'last_chat_test':provider.status}
    local=urlparse(provider.base_url).hostname in TRUSTED_HOSTS
    if provider.kind=='codex_cli':
        executable=shutil.which(os.environ.get('FOREST_CODEX_EXECUTABLE','codex'))
        try:
            result['available']=bool(executable and subprocess.run([executable,'login','status'],capture_output=True,timeout=3).returncode==0)
            result['reason']='Local CLI login check only; model inference is not probed' if result['available'] else 'Install and sign in to Codex CLI'
        except (OSError,subprocess.TimeoutExpired): result['reason']='Local CLI login check unavailable'
    elif local or provider.allow_paid:
        try:
            endpoint=provider.base_url.rstrip('/')+('/api/tags' if provider.kind=='ollama' else '/models')
            credential=read_secret(provider.credential_ref)
            response=httpx.get(endpoint,headers={'Authorization':'Bearer '+credential} if credential else {},timeout=2)
            response.raise_for_status(); data=response.json()
            names=[m.get('name','') for m in data.get('models',[])] if provider.kind=='ollama' else [m.get('id','') for m in data.get('data',[])]
            result['available']=provider.model in names or (provider.kind=='ollama' and provider.model+':latest' in names)
            if not result['available']: result['reason']='Configured model is absent from the current model list'
        except Exception as exc: result['reason']=type(exc).__name__
    else: result['reason']='External provider is disabled'
    if len(_provider_health_cache)>100: _provider_health_cache.clear()
    _provider_health_cache[key]=(time.monotonic(),result)
    return result
@app.get('/api/system')
def system():
    vm=psutil.virtual_memory(); disk=psutil.disk_usage(str(settings.data_dir)); gpu=[]
    if shutil.which('nvidia-smi'):
        try:
            r=subprocess.run(['nvidia-smi','--query-gpu=name,memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
            if r.returncode==0: gpu=[dict(zip(['name','memory_used_mb','memory_total_mb','utilization_percent'],[v.strip() for v in line.split(',')])) for line in r.stdout.splitlines()]
        except (OSError,subprocess.TimeoutExpired): pass
    with Session() as s:
        workers=[asdict(w) for w in s.scalars(select(Worker))]; providers=list(s.scalars(select(Provider)))
    for w in workers: w['online']=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(w['heartbeat'])).total_seconds()<10
    provider_status=[provider_health(p) for p in providers]
    verified_model=any(p['available'] and provider.kind!='codex_cli' for p,provider in zip(provider_status,providers))
    codex_cli_signed_in=any(p['available'] and provider.kind=='codex_cli' for p,provider in zip(provider_status,providers))
    return {'cpu_percent':psutil.cpu_percent(),'memory':{'total':vm.total,'used':vm.used,'percent':vm.percent},'disk':{'total':disk.total,'used':disk.used,'free':disk.free},'gpu':gpu,'workers':workers,'latex':shutil.which('tectonic') or shutil.which('pdflatex'),'model_connected':verified_model,'model_check_pending':codex_cli_signed_in and not verified_model,'provider_status':provider_status,'platform':os.uname().sysname,'isolation':'trusted_local_process'}
DEFAULT_SETTINGS = {'language':'en','theme':'light','retention_days':30,'default_mode':'assisted'}

@app.get('/api/settings')
def read_settings():
    with Session() as s: p=s.get(Preference,'settings'); return p.value if p else dict(DEFAULT_SETTINGS)
@app.patch('/api/settings')
def update_settings(body:dict=Body(...)):
    with Session.begin() as s:
        p=s.get(Preference,'settings')
        if not p: p=Preference(key='settings',value=dict(DEFAULT_SETTINGS)); s.add(p)
        p.value={**p.value,**body}; return p.value
@app.post('/api/settings/cleanup')
def cleanup(body:dict=Body(...)):
    cutoff=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=max(0,int(body.get('days',30))))).isoformat(); removed=0
    project_scope=body.get('project_id')
    with Session() as reader:
        projects=list(reader.scalars(select(Project.id).where(Project.id==project_scope) if project_scope and not body.get('clear_edit_history') else select(Project.id)))
    for project_id in sorted(projects):
        with Session.begin() as s:
            from services.worker.scheduler import _lock_project
            project=_lock_project(s,project_id)
            if not project_scope or project_id==project_scope:
                for run in s.scalars(select(TaskRun).where(TaskRun.project_id==project_id,
                        TaskRun.finished_at<cutoff,TaskRun.status.in_(TERMINAL)).with_for_update()):
                    from services.interventions.controls import close_unconsumed
                    # Keep the run and cached observations retryable until disk removal succeeds.
                    if run.output_path:
                        output=safe_path(project_dir(project_id),run.output_path)
                        if output==project_dir(project_id).resolve():
                            error('CLEANUP_FAILED','Refusing to remove the project directory',500)
                        try:
                            shutil.rmtree(output)
                        except FileNotFoundError:
                            # A prior attempt may have removed files before its DB transaction failed.
                            if output.exists():
                                error('CLEANUP_FAILED',f'Could not remove output for run {run.id}',500,retryable=True)
                        except OSError:
                            error('CLEANUP_FAILED',f'Could not remove output for run {run.id}',500,retryable=True)
                    # Observation writers acquire this same project lock before updating scopes.
                    # Deleting their parent rows cascades to cached ObservedFile bytes.
                    from services.observation.models import ObservationScope
                    s.execute(delete(ObservationScope).where(ObservationScope.project_id==project_id,
                        ObservationScope.object_id==run.id,
                        ObservationScope.kind.in_(('run_output','run_workspace','remote_workspace'))))
                    close_unconsumed(s,run)
                    touch_dependents(s,project_id,run.id); s.delete(run); removed+=1
            if body.get('clear_edit_history'):
                project.graph_meta={key:value for key,value in project.graph_meta.items() if key!='_history'}
    return {'deleted_runs':removed}
@app.get('/api/providers')
def providers():
    with Session() as s: return [asdict(p) for p in s.scalars(select(Provider))]

def provider_references(s,ident):
    refs=[]
    for project in s.scalars(select(Project)):
        if (project.config or {}).get('provider_id')==ident:
            refs.append({'kind':'project','id':project.id,'name':project.name,'location':'project settings'})
    for node in s.scalars(select(Node)):
        if (node.config or {}).get('provider_id')==ident:
            project=s.get(Project,node.project_id)
            refs.append({'kind':'node','id':node.id,'name':node.title,'location':f"{project.name if project else node.project_id} / node settings"})
    for agent in s.scalars(select(Agent).where(Agent.provider_id==ident)):
        refs.append({'kind':'agent','id':agent.id,'name':agent.name,'location':f'Agent role: {agent.role}'})
    from services.observation.models import ReporterPolicy
    for policy in s.scalars(select(ReporterPolicy)):
        if (policy.settings or {}).get('provider_id')==ident:
            project=s.get(Project,policy.project_id)
            refs.append({'kind':'reporter','id':policy.project_id,'name':project.name if project else policy.project_id,'location':'reporting settings'})
    preference=s.get(Preference,'settings')
    if preference and (preference.value or {}).get('default_provider_id')==ident:
        refs.append({'kind':'default','id':'settings','name':'Global default','location':'new projects and runs'})
    history=s.scalar(select(func.count(ModelRequest.id)).where(ModelRequest.provider_id==ident)) or 0
    return {'references':refs,'historical_request_count':history}

@app.get('/api/providers/{ident}/references')
def get_provider_references(ident:str):
    with Session() as s:
        get(s,Provider,ident)
        return provider_references(s,ident)

@app.post('/api/providers/{ident}/retire')
def provider_retire(ident:str,body:dict=Body(default={} )):
    replacement_id=body.get('replacement_provider_id')
    credential_ref=None
    with Session.begin() as s:
        p=get(s,Provider,ident,for_update=True)
        replacement=None
        if replacement_id:
            if replacement_id==ident: error('INVALID_REPLACEMENT','Choose a different active provider',422)
            replacement=get(s,Provider,replacement_id)
            if replacement.status=='retired': error('INVALID_REPLACEMENT','Replacement provider must be active',422)
        snapshot=provider_references(s,ident)
        for project in s.scalars(select(Project)):
            config=dict(project.config or {})
            if config.get('provider_id')==ident:
                if replacement: config['provider_id']=replacement.id
                else: config.pop('provider_id',None)
                project.config=config
                # A client holding the earlier revision must not restore the retired provider.
                project.revision+=1; emit(s,project.id,'project_changed',{'revision':project.revision})
        for node in s.scalars(select(Node)):
            config=dict(node.config or {})
            if config.get('provider_id')==ident:
                if replacement: config['provider_id']=replacement.id
                else: config.pop('provider_id',None)
                node.config=config; node.revision+=1
        for agent in s.scalars(select(Agent).where(Agent.provider_id==ident)):
            agent.provider_id=replacement.id if replacement else None
        from services.observation.models import ReporterPolicy
        for policy in s.scalars(select(ReporterPolicy)):
            settings_value=dict(policy.settings or {})
            if settings_value.get('provider_id')==ident:
                if replacement: settings_value['provider_id']=replacement.id
                else: settings_value.pop('provider_id',None)
                policy.settings=settings_value
        preference=s.get(Preference,'settings')
        if preference and (preference.value or {}).get('default_provider_id')==ident:
            values=dict(preference.value or {})
            if replacement: values['default_provider_id']=replacement.id
            else: values.pop('default_provider_id',None)
            preference.value=values
        credential_ref=p.credential_ref
        p.credential_ref=None
        p.status='retired'
        result={'provider':asdict(p),'updated_reference_count':len(snapshot['references']),'historical_request_count':snapshot['historical_request_count']}
    delete_secret(credential_ref)
    return result

@app.post('/api/providers/{ident}/restore')
def provider_restore(ident:str):
    with Session.begin() as s:
        p=get(s,Provider,ident,for_update=True)
        if p.status=='retired': p.status='untested'
        return asdict(p)

@app.post('/api/providers')
def provider_create(body:dict=Body(...)):
    if body.get('kind','openai') not in ('ollama','openai','codex_cli'): error('INVALID_PROVIDER','Use ollama, openai-compatible or codex_cli')
    if urlparse(body.get('base_url','')).scheme not in ('http','https'): error('INVALID_URL','An HTTP(S) base URL is required')
    if body.get('kind')=='codex_cli' and urlparse(body.get('base_url','')).hostname not in TRUSTED_HOSTS:
        error('INVALID_URL','Codex CLI uses the local login; configure a localhost placeholder URL')
    with Session.begin() as s:
        key=body.pop('api_key',None); p=Provider(**{k:body[k] for k in ('name','kind','base_url','model','allow_paid','config') if k in body}); s.add(p)
        if key: p.credential_ref=put_secret(key)
        s.flush(); return asdict(p)
@app.patch('/api/providers/{ident}')
def provider_edit(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        p=get(s,Provider,ident)
        for k in ('name','kind','base_url','model','allow_paid','config'):
            if k in body: setattr(p,k,body[k])
        if body.get('api_key'): p.credential_ref=put_secret(body['api_key'],p.credential_ref)
        p.status='untested'; return asdict(p)
@app.post('/api/providers/{ident}/test')
def provider_test(ident:str):
    from research.agents.provider import ModelClient
    with Session() as s:
        p=get(s,Provider,ident)
        if p.status=='retired': error('PROVIDER_RETIRED','Restore this provider before testing it',409)
        d=asdict(p,True)
    try:
        result=ModelClient(d,read_secret(d.get('credential_ref')),d.get('allow_paid',False)).complete([{'role':'user','content':'Return JSON {"status":"connected"}.'}]); status='connected'
    except Exception as exc: result={'error':str(exc)}; status='failed'
    with Session.begin() as s: get(s,Provider,ident).status=status
    if status=='failed': error('PROVIDER_CONNECTION_FAILED',result['error'],502,'Check endpoint, model identifier and credentials.',True)
    request_status=result.get('status')
    return {**result,'status':status,'request_status':request_status}
@app.get('/api/providers/{ident}/usage')
def provider_usage(ident:str):
    from research.agents.budget import usage_summary
    with Session() as s: get(s,Provider,ident)
    return usage_summary(ident)
@app.get('/api/providers/{ident}/models')
def provider_models(ident:str):
    from research.agents.provider import ModelClient
    with Session() as s:
        provider=get(s,Provider,ident)
        if provider.status=='retired': error('PROVIDER_RETIRED','Restore this provider before loading its models',409)
        p=asdict(provider,True)
    return ModelClient(p,read_secret(p.get('credential_ref')),p.get('allow_paid',False)).models()
@app.get('/api/hosts')
def hosts():
    with Session() as s: return [asdict(p) for p in s.scalars(select(Host))]
@app.post('/api/hosts')
def host_create(body:dict=Body(...)):
    with Session.begin() as s: h=Host(**{k:body[k] for k in ('name','kind','config') if k in body}); s.add(h); s.flush(); return asdict(h)
@app.patch('/api/hosts/{ident}')
def host_edit(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        h=get(s,Host,ident)
        for k in ('name','kind','config'):
            if k in body: setattr(h,k,body[k])
        h.status='untested'; return asdict(h)
@app.post('/api/hosts/{ident}/test')
def host_test(ident:str):
    with Session() as s: h=asdict(get(s,Host,ident))
    try:
        if h['kind']=='local': result={'connected':True,'platform':os.uname().sysname}
        else:
            from runners.remote import RemoteRunner
            result=RemoteRunner(h['config']).test()
        with Session.begin() as s: get(s,Host,ident).status='connected'
        return result
    except Exception as exc:
        with Session.begin() as s: get(s,Host,ident).status='failed'
        error('HOST_CONNECTION_FAILED',str(exc),502)
@app.get('/api/agents')
def agents():
    with Session() as s: return [asdict(a) for a in s.scalars(select(Agent))]
@app.post('/api/agents')
def agent_create(body:dict=Body(...)):
    from research.agents.tool_policy import validate_permissions
    try:
        validate_permissions({key: body[key] for key in ('tools',) if key in body})
        validate_permissions(body.get('config', {}))
    except ValueError as exc: error('INVALID_TOOL_POLICY', str(exc), 422)
    values={k:body[k] for k in ('name','role','instructions','provider_id','tools','config','enabled') if k in body}
    values['config']=explicit_agent_config(values.get('config'))
    with Session.begin() as s:
        if values.get('provider_id') and not s.get(Provider,values['provider_id']): error('PROVIDER_NOT_FOUND','Selected provider does not exist',422)
        a=Agent(**values); s.add(a); s.flush(); return asdict(a)
@app.patch('/api/agents/{ident}')
def agent_edit(ident:str,body:dict=Body(...)):
    from research.agents.tool_policy import validate_permissions
    try:
        validate_permissions({key: body[key] for key in ('tools',) if key in body})
        validate_permissions(body.get('config', {}))
    except ValueError as exc: error('INVALID_TOOL_POLICY', str(exc), 422)
    with Session.begin() as s:
        a=get(s,Agent,ident,for_update=True)
        if body.get('provider_id') and not s.get(Provider,body['provider_id']): error('PROVIDER_NOT_FOUND','Selected provider does not exist',422)
        customized=bool((a.config or {}).get('tools_customized')) or 'tools' in body
        for k in ('name','role','instructions','provider_id','tools','config','enabled'):
            if k in body: setattr(a,k,body[k])
        if customized: a.config=explicit_agent_config(a.config)
        return asdict(a)
@app.post('/api/projects/{ident}/share')
def share(ident:str,body:dict=Body(default={})):
    with Session.begin() as s:
        get(s,Project,ident); item=Share(project_id=ident,token=secrets.token_urlsafe(32),selection=body); s.add(item); s.flush(); return {'id':item.id,'url':'/share/'+item.token,'token':item.token}
@app.delete('/api/shares/{token}')
def revoke_share(token:str,request:Request):
    if not authorized(request): error('UNAUTHORIZED','Owner authentication required',401)
    with Session.begin() as s:
        item=s.scalar(select(Share).where(Share.token==token))
        if not item: error('NOT_FOUND','Share missing',404)
        item.enabled=False
    return {'revoked':True}
@app.get('/api/shares/{token}')
def shared(token:str):
    with Session() as s:
        item=s.scalar(select(Share).where(Share.token==token,Share.enabled==True))
        if not item: error('NOT_FOUND','Share unavailable',404)
        p=get(s,Project,item.project_id); g=graph_from_db(s,p)
        allowed=item.selection.get('node_ids',[])
        nodes=[{k:n[k] for k in ('id','title','type','execution_status','research_status','deliverable_status','position')} for n in g['nodes'] if n['id'] in allowed]
        return {'project':{'name':p.name,'description':p.description if item.selection.get('description') else ''},'nodes':nodes,'read_only':True}
@app.websocket('/ws/projects/{ident}/terminal')
async def terminal(ws:WebSocket,ident:str):
    origin=ws.headers.get('origin'); local=ws.client and ws.client.host in ('127.0.0.1','::1')
    if (origin and urlparse(origin).hostname not in TRUSTED_HOSTS and urlparse(origin).hostname!=ws.url.hostname) or (ws.cookies.get('forest_owner')!=owner_token() and not local): await ws.close(code=1008); return
    with Session() as s:
        p=s.get(Project,ident)
        if not p: await ws.close(code=1008); return
        branch=s.scalar(select(Branch).where(Branch.project_id==ident,Branch.is_main==True)); cwd=safe_path(project_dir(ident),branch.workspace if branch else '.')
    from .terminal import attach
    await attach(ws,ident+':'+str(branch.id if branch else 'main'),cwd)
from .progress import router as progress_router
app.include_router(progress_router)
app.include_router(files_router)
from .research_runtime import router as runtime_router
# Specific research endpoints must precede resources' /research/{action}.
app.include_router(runtime_router)
from .research_analysis import router as analysis_router
app.include_router(analysis_router)
from .resources import router as research_router
app.include_router(research_router)
from .repositories import router as repositories_router
app.include_router(repositories_router)
from .run_diagnostics import router as diagnostics_router
app.include_router(diagnostics_router)
from .verification import router as verification_router
app.include_router(verification_router)
from .interventions import router as interventions_router
app.include_router(interventions_router)
from .research_evolution import router as evolution_router
app.include_router(evolution_router)

def api_openapi():
    """Describe the existing API without changing handler validation or output."""
    if app.openapi_schema is None:
        from fastapi.openapi.utils import get_openapi
        from .contracts import enhance_openapi
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        # FastAPI may retain included routers as nested route containers.
        # Generate with its native traversal before excluding the Web fallback.
        schema['paths'] = {path: value for path, value in schema['paths'].items() if path.startswith('/api/')}
        app.openapi_schema = enhance_openapi(schema)
    return app.openapi_schema

app.openapi = api_openapi
web_dist=Path(__file__).resolve().parents[2]/'apps/web/dist'
if web_dist.exists():
    app.mount('/assets',StaticFiles(directory=web_dist/'assets'),name='assets')
    @app.get('/{path:path}')
    def web(path:str):
        if path.startswith('api/') or path.startswith('ws/'): error('NOT_FOUND','API endpoint does not exist',404)
        requested=safe_path(web_dist,path)
        return FileResponse(requested if requested.is_file() else web_dist/'index.html')
