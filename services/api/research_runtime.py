"""Editable research session, evidence lineage and run recovery views."""
import json
import filecmp
from fastapi import APIRouter, Body
from sqlalchemy import func, literal, or_, select, union_all
from .db import *
from .common import get,project_dir,safe_path,emit,error
router=APIRouter()

@router.get('/api/projects/{ident}/research/route-health')
def research_route_health(ident:str):
    from .common import graph_from_db
    from research.planning.route_review import route_health
    with Session() as s:
        project=get(s,Project,ident)
        runs=list(s.scalars(select(TaskRun).where(TaskRun.project_id==ident)))
        return route_health(graph_from_db(s,project),[asdict(run) for run in runs],project.config)

@router.post('/api/projects/{ident}/research/route-review')
def review_research_route(ident:str,body:dict=Body(default={})):
    from services.worker.scheduler import enqueue
    from .common import graph_from_db
    from research.planning.route_review import route_health
    with Session.begin() as s:
        project=get(s,Project,ident)
        runs=list(s.scalars(select(TaskRun).where(TaskRun.project_id==ident)))
        health=route_health(graph_from_db(s,project),[asdict(run) for run in runs],project.config)
        run=enqueue(s,ident,'research_route_review',body,body.get('request_id'))
        control=project.config.get('controller',{})
        if control.get('route_review',{}).get('run_id')!=run.id:
            project.config={**project.config,'controller':{**control,'route_review':{
                'run_id':run.id,'trigger_run_ids':sorted({rid for signal in health['signals'] for rid in signal['run_ids']}),
                'covered_run_ids':control.get('route_review',{}).get('covered_run_ids',[]),'consumed':False}}}
        return asdict(run)

@router.get('/api/writing-policy')
def writing_policy():
    from research.paper.style import writing_profile, writing_contract
    return {**writing_profile(), 'manuscript_contract':writing_contract(), 'revision_contract':writing_contract('revision')}

@router.get('/api/publication-profile')
def default_publication_profile():
    from research.publication import publication_profile,publication_instructions
    from research.agents.policy import ROLES
    profile=publication_profile()
    return {'profile':profile,'instructions':publication_instructions(profile),'roles':list(ROLES)}

@router.get('/api/projects/{ident}/publication')
def publication_state(ident:str):
    from research.planning.loop import submission_audit
    with Session() as s:
        p=get(s,Project,ident)
        return submission_audit(s,p)

@router.patch('/api/projects/{ident}/publication')
def configure_publication(ident:str,body:dict=Body(...)):
    from research.publication import publication_profile
    with Session.begin() as s:
        p=get(s,Project,ident)
        current=p.config.get('publication_profile',{})
        if isinstance(current,str):current={'id':current}
        try:profile=publication_profile({'publication_profile':{**current,**body}})
        except ValueError as exc:error('INVALID_PUBLICATION_PROFILE',str(exc),422)
        p.config={**p.config,'publication_profile':profile};p.revision+=1
        emit(s,ident,'project_changed',{'revision':p.revision,'publication_profile':profile})
        return {'profile':profile,'revision':p.revision}

@router.get('/api/projects/{ident}/research')
def research_state(ident:str,overview:bool=False):
    with Session() as s:
        p=get(s,Project,ident)
        if overview:
            # Overview controls need recorded lifecycle, not a replay of every
            # historical verifier. Explicit comparison reads keep the checker.
            active=list(s.scalars(select(TaskRun).where(TaskRun.project_id==ident,
                TaskRun.status.in_(['queued','running','waiting','waiting_input','paused','pausing','budget_exhausted'])).order_by(TaskRun.created_at.desc()).limit(100)))
            decisions=list(s.scalars(select(Hypothesis).where(Hypothesis.project_id==ident).order_by(Hypothesis.created_at.desc()).limit(20)))
            counts=dict(s.execute(select(TaskRun.status,func.count()).where(TaskRun.project_id==ident).group_by(TaskRun.status)).all())
            return {'objective':p.config.get('objective',{}),'trials':[],'controller':p.config.get('controller',{}),
                    'active_runs':[{'id':r.id,'kind':r.kind,'status':r.status,'node_id':r.node_id,'resource':r.resource} for r in active],
                    'decisions':[asdict(x) for x in decisions if x.data.get('origin')=='research_controller'],
                    'counts':{'runs':sum(counts.values()),'completed':counts.get('completed',0),'failed':counts.get('failed',0)+counts.get('interrupted',0)},
                    'coverage':'Recorded lifecycle only; at most 100 active runs and 20 recent decisions. Comparisons require explicit inspection.'}
        decisions=list(s.scalars(select(Hypothesis).where(Hypothesis.project_id==ident).order_by(Hypothesis.created_at.desc())))
        runs=list(s.scalars(select(TaskRun).where(TaskRun.project_id==ident).order_by(TaskRun.created_at.desc())))
        active=[{'id':r.id,'kind':r.kind,'status':r.status,'node_id':r.node_id,'resource':r.resource} for r in runs if r.status in ('queued','running','waiting','waiting_input','paused','pausing','budget_exhausted')]
        from research.planning.scoreboard import compare_trials
        from services.interventions.applicability import goal_applicability
        from services.api.verification import verification_for_run,numerical_coverage_for_run,required_policy
        trials=compare_trials([{'id':r.id,'kind':r.kind,'status':r.status,'metrics':r.metrics,'config':r.config,'created_at':r.created_at,
                               'verification_status':verification_for_run(s,r)['verification_status'], 'goal_applicability':goal_applicability(s,r),
                               'numerical_verification':numerical_coverage_for_run(s,r),
                               'verification_policy':'required' if required_policy(s,r) else 'optional'} for r in runs],p.config.get('objective'))
        return {'objective':p.config.get('objective',{}),'trials':trials,'controller':p.config.get('controller',{}),'active_runs':active,'decisions':[asdict(x) for x in decisions if x.data.get('origin')=='research_controller'], 'counts':{'runs':len(runs),'completed':sum(r.status=='completed' for r in runs),'failed':sum(r.status in ('failed','interrupted') for r in runs)}}

@router.get('/api/projects/{ident}/usage')
def project_usage(ident:str):
    """Read project spending and report account-wide provider limits separately."""
    from research.agents.budget import cost_summary
    with Session() as s:
        project=get(s,Project,ident)
        rows=list(s.scalars(select(ModelRequest).where(ModelRequest.project_id==ident)))
        runs=list(s.scalars(select(TaskRun).where(TaskRun.project_id==ident)))
        limit=project.budget.get('cost_usd')
        provider_ids={row.provider_id for row in rows}
        if project.config.get('provider_id'): provider_ids.add(project.config['provider_id'])
        providers=[]
        for provider_id in sorted(provider_ids):
            provider=s.get(Provider,provider_id)
            if not provider: continue
            provider_rows=list(s.scalars(select(ModelRequest).where(ModelRequest.provider_id==provider_id)))
            provider_limit=provider.config.get('budget_usd')
            providers.append({'id':provider.id,'name':provider.name,'scope':'all_projects',
                'allow_paid':provider.allow_paid,'limit_usd':provider_limit,
                **cost_summary(provider_rows, provider_limit, unpriced=provider.kind=='codex_cli')})
        elapsed=sum(float(run.resource.get('elapsed_seconds',0)) for run in runs)
        return {'project_id':ident,'allow_paid':bool(project.budget.get('allow_paid',False)),
            'limits':project.budget,**cost_summary(rows, limit),'uncertain_requests':sum(row.status=='uncertain' for row in rows),
            'run_count':len(runs),'elapsed_seconds':elapsed,'providers':providers,
            'active_run_limits':[{'run_id':run.id,'status':run.status,'agent_budget':run.config.get('agent_budget',{})}
                for run in runs if run.status in ('queued','running','waiting','waiting_input','paused','pausing','budget_exhausted')]}

@router.get('/api/runs/{ident}/lineage')
def run_lineage(ident:str):
    with Session() as s:
        original=get(s,TaskRun,ident); pending=[ident]; seen=set(); rows=[]
        while pending:
            rid=pending.pop()
            if rid in seen: continue
            seen.add(rid); r=s.get(TaskRun,rid)
            if not r or r.project_id!=original.project_id:
                rows.append({'id':rid,'missing':True}); continue
            n=s.get(Node,r.node_id) if r.node_id else None
            cfg={k:v for k,v in r.config.items() if k not in ('provider_snapshot','env')}
            files=[]; root=project_dir(r.project_id); folder=safe_path(root,r.output_path)
            if folder.exists():
                files=[{'path':str(x.relative_to(root)),'bytes':x.stat().st_size} for x in folder.rglob('*') if x.is_file() and not x.is_symlink()]
            freshness=[]
            branch=s.get(Branch,r.branch_id) if r.branch_id else None
            if branch:
                working=safe_path(root,branch.workspace)
                for saved in (folder/'workspace').rglob('*'):
                    if saved.is_file() and not saved.is_symlink() and not any(x.startswith('.') for x in saved.relative_to(folder/'workspace').parts):
                        relative=saved.relative_to(folder/'workspace'); current=safe_path(working,str(relative))
                        if current.is_file():
                            same=filecmp.cmp(saved,current,shallow=False)
                            freshness.append({'path':str(relative),'current':same,'status':'matches_working_file' if same else 'working_file_changed'})
            rows.append({'source_freshness':freshness,'id':r.id,'node_id':r.node_id,'node_title':cfg.get('node_title'),'status':r.status,'node_revision':r.node_revision,'current_node_revision':n.revision if n else None,'current':bool(n and n.revision==r.node_revision and n.extra.get('results_current',True)),'dependencies':r.dependencies,'config':cfg,'metrics':r.metrics,'resource':r.resource,'files':files})
            pending.extend(r.dependencies)
        return {'run_id':ident,'project_id':original.project_id,'runs':rows}

@router.get('/api/runs/{ident}/session')
def agent_session(ident:str,summary:bool=False):
    with Session() as s:
        run=get(s,TaskRun,ident); folder=safe_path(project_dir(run.project_id),run.output_path+'/workspace')
        path=folder/'agent_session.json'
        if not path.exists(): return {'run_id':ident,'status':run.status,'session':None}
        data=json.loads(path.read_text())
        # Cancellation is confirmed by the run controller after the Agent
        # process has stopped. The last session checkpoint may predate that
        # confirmation, so expose the authoritative terminal run state.
        if run.status == 'cancelled': data={**data,'status':'cancelled'}
        # Scientific checkpoints describe research state. Resource accounting
        # belongs to the independent ledger, including for legacy readback.
        for key in ('totals','active_seconds','cost_complete','usage_complete'):
            data.pop(key,None)
        if summary:
            usage_path=folder.parent/'agent_usage.json'
            if not usage_path.exists(): usage_path=folder/'agent_usage.json'
            usage=None; usage_error=None
            if usage_path.exists():
                try:
                    ledger=json.loads(usage_path.read_text())
                except (OSError,ValueError,UnicodeError) as exc:
                    usage_error={'code':'LEDGER_UNREADABLE','message':'Resource ledger cannot be read; reconcile the original supervisor. '+str(exc)}
                else:
                    if (isinstance(ledger,dict) and ledger.get('run_id')==ident
                            and isinstance(ledger.get('totals'),dict)):
                        usage={key:ledger.get(key) for key in ('totals','active_seconds','cost_complete','usage_complete','updated_at')}
                    else:
                        usage_error={'code':'LEDGER_INVALID','message':'Resource ledger does not bind this run or has invalid totals; reconcile the original supervisor.'}
            data={**{k:data.get(k) for k in ('status','wait_for','updated_at','budget_reason','lifecycle','round','recovery','goal_contract','reconciliation_reason')},
                  'usage':usage,'usage_error':usage_error,'transcript_count':len(data.get('transcript',[]))}
        return {'run_id':ident,'status':run.status,'session':data}

@router.patch('/api/runs/{ident}/configuration')
def configure_run(ident:str,body:dict=Body(...)):
    from research.agents.tool_policy import validate_permissions
    try: validate_permissions(body)
    except ValueError as exc: error('INVALID_TOOL_POLICY', str(exc), 422)
    with Session() as reader: project_id=get(reader,TaskRun,ident).project_id
    with Session.begin() as s:
        from services.worker.scheduler import _lock_project
        _lock_project(s,project_id)
        run=get(s,TaskRun,ident,for_update=True)
        # Execution identities belong to the runner; research parameters remain editable.
        blocked={'provider_snapshot','execution_attempt','resolved_inputs','project_goal','_repository_source',
                 '_verification_binding','_verification_contract','_verification_sources',
                 'verification_result','verification_status','analysis_snapshot','analysis_intent',
                 'analysis_scope','analysis_node_id','analysis_branch_id','analysis_project_revision'}
        if blocked & body.keys(): error('INVALID_CONFIGURATION','Execution identity fields are managed by the runner',422)
        if run.kind=='research_analysis' and set(body)-{'budget','agent_budget','timeout','priority'}:
            error('ANALYSIS_INTENT_BOUND','Submit a new analysis to change its question, scope, literature or model selection',409)
        if 'repository' in body:
            from research.execution.repository import normalize_repository, RepositoryError
            if run.kind not in ('agent','command','experiment','repository_clone'):
                error('INVALID_REPOSITORY','This task kind does not accept repository inputs',422)
            try: body={**body,'repository':normalize_repository(body['repository'])}
            except RepositoryError as exc:error(exc.code,str(exc),422)
        if 'provider_id' in body and body['provider_id']!=run.config.get('provider_id'):
            if run.started_at or run.config.get('execution_attempt'):
                error('PROVIDER_BOUND','Started runs retain their provider snapshot; enqueue a new run to change provider',409)
            provider=get(s,Provider,body['provider_id'])
            run.config={**run.config,'provider_snapshot':{**asdict(provider,secrets=True),
                '_usage_context':{'project_id':project_id,'run_id':run.id}},
                'provider_selection':{'provider_id':provider.id,'source':'run_configuration'}}
        if 'budget' in body:
            from services.api.common import validate_project_budget
            validate_project_budget(body['budget'])
        if 'timeout' in body and body['timeout'] is not None:
            import math
            value=body['timeout']
            if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
                error('INVALID_TIMEOUT','Task timeout must be a finite positive number or null',422)
        changed={key for key,value in body.items() if value!=run.config.get(key)}
        operational={'budget','agent_budget','timeout','priority','allow_paid','context_char_budget'}
        if changed-operational and (run.started_at or run.config.get('execution_attempt')):
            run.resource={**run.resource,'configuration_changed_after_execution':True,'verification_status':'unverified'}
        if run.kind=='verification' and changed:
            resource={**run.resource,'verification_status':'unverified'}
            resource.pop('verification_receipt',None)
            run.resource=resource
        run.config={**run.config,**body}
        emit(s,run.project_id,'run_changed',{'run_id':ident,'status':run.status,'configuration_updated':True})
        return {'run_id':ident,'config':{k:v for k,v in run.config.items() if k not in ('provider_snapshot','env')}}

@router.get('/api/projects/{ident}/graph/page')
def graph_page(ident:str,offset:int=0,limit:int=100,branch_id:str|None=None,query:str=''):
    with Session() as s:
        p=get(s,Project,ident); stmt=select(Node).where(Node.project_id==ident)
        if branch_id: stmt=stmt.where(Node.branch_id==branch_id)
        if query: stmt=stmt.where(Node.title.ilike('%'+query+'%'))
        nodes=list(s.scalars(stmt.order_by(Node.created_at,Node.id).offset(max(offset,0)).limit(min(max(limit,1),500))))
        ids={n.id for n in nodes}
        if ids:
            page_ids=union_all(*(select(literal(node_id).label('id')) for node_id in ids)).cte('page_ids')
            page_id_select=select(page_ids.c.id)
            edges=list(s.scalars(select(Edge).where(Edge.project_id==ident,or_(Edge.source.in_(page_id_select),Edge.target.in_(page_id_select)))))
        else: edges=[]
        return {'revision':p.revision,'offset':offset,'nodes':[asdict(n) for n in nodes],'edges':[asdict(e) for e in edges],'next_offset':offset+len(nodes) if len(nodes)==min(max(limit,1),500) else None}

@router.post('/api/projects/{ident}/protocol/validate')
def validate_protocol(ident:str,body:dict=Body(...)):
    from research.validation.protocol import validate_experiment,validate_submission_design
    from research.publication import publication_profile
    with Session() as s: profile=publication_profile(get(s,Project,ident).config)
    try:
        protocol=validate_experiment(body)
        if profile['id']=='full_submission':protocol=validate_submission_design(protocol,profile)
        return {'protocol':protocol,'status':'structurally_checked','publication_profile':profile,
                'verification_scope':'Protocol structure only; real execution, source verification and independent review remain required.'}
    except ValueError as exc: error('INVALID_PROTOCOL',str(exc),422)

@router.post('/api/projects/{ident}/writing/review')
def review_writing(ident:str,body:dict=Body(...)):
    from research.paper.writing import review_defensive_writing
    with Session() as s: get(s,Project,ident)
    return review_defensive_writing(body.get('source',''))

@router.post('/api/projects/{ident}/graph/batch')
def graph_batch(ident:str,body:dict=Body(...)):
    from services.interventions.application import apply_commands
    commands=body.get('commands',[])
    if not isinstance(commands,list) or not commands: error('EMPTY_BATCH','Provide graph commands',422)
    allowed={'add_node','edit_node','add_dependency','remove_dependency','prune_branch','restore_branch','set_main_branch'}
    if any(not isinstance(command,dict) or command.get('operation') not in allowed for command in commands):
        error('UNSUPPORTED_BATCH_OPERATION','Use a separate command for this operation',422)
    return apply_commands(ident,body.get('request_id') or uid(),body.get('expected_revision'),commands,batch=True)

@router.post('/api/projects/{ident}/statistics/review')
def review_statistics(ident:str,body:dict=Body(default={})):
    from services.worker.scheduler import enqueue
    with Session.begin() as s:
        get(s,Project,ident)
        return asdict(enqueue(s,ident,'review',{**body,'review_scope':'statistics'},body.get('request_id')))

@router.post('/api/projects/{ident}/statistics/paired')
def paired_statistics(ident:str,body:dict=Body(...)):
    from services.worker.scheduler import enqueue
    for key in ('path','unit_column','baseline_column','candidate_column'):
        if not body.get(key):error('MISSING_FIELD',key+' is required',422)
    with Session.begin() as s:
        get(s,Project,ident);safe_path(project_dir(ident),body['path'],True)
        return asdict(enqueue(s,ident,'analysis',{**body,'analysis_type':'paired'},body.get('request_id')))

@router.patch('/api/projects/{ident}/objective')
def edit_objective(ident:str,body:dict=Body(...)):
    metric=str(body.get('metric','')).strip()
    if body.get('direction','min') not in ('min','max'):error('INVALID_OBJECTIVE','Choose min or max',422)
    with Session.begin() as s:
        p=get(s,Project,ident)
        p.config={**p.config,'objective':({'metric':metric,'direction':body.get('direction','min'),'comparison_fields':body.get('comparison_fields',['dataset','protocol_version'])} if metric else {})}
        p.revision+=1;emit(s,ident,'project_changed',{'revision':p.revision})
        return p.config['objective']
