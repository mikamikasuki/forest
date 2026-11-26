#!/usr/bin/env python3
"""Release qualification through the actual public API and durable worker.

No model substitution, injected solution, hidden success fallback, hashes, or
frozen artifacts. The API server owns provider credentials and spend control.
"""
from __future__ import annotations
import argparse
import ast
import fcntl
import signal
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
import zipfile
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.qualification.tasks import VERSION, ORACLE_REVISION, corpus, check, result_types, task_instruction

AGENT_CONTEXT_CHARS=24000
TERMINAL={'completed','failed','interrupted','cancelled','skipped','budget_exhausted','waiting_input'}
PRIME_GOAL='Operational verification, not a novel scientific contribution: create the simplest executable research path with one Engineer Agent node. That node must implement two independent Python prime-count algorithms (trial division and sieve), actually run both for integers <=10000, compare their counts for exact equality, and save metrics.json plus report.md. Read the actual output before reporting it. Do not look up prime counts or use precomputed numbers. No literature search is needed. The node config must include kind=agent, role=Engineer, expected_outputs=["metrics.json","report.md"], metrics_file=metrics.json. After the completed measured run, cite that actual run ID and conclude completed. Start from this empty graph; do not assume code or nodes exist.'


def utc(): return datetime.now(timezone.utc).isoformat()

def save(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp'); temporary.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n'); temporary.replace(path)


@contextmanager
def report_lock(path):
    """An OS lock prevents two monitors from mutating one report or launching work."""
    lock_path=Path(str(path)+'.monitor.lock'); lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a+') as handle:
        try: fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another monitor owns this report: '+str(path))
        handle.seek(0); handle.truncate(); json.dump({'pid':os.getpid(),'started_at':utc()},handle); handle.flush()
        try: yield
        finally: fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


class API:
    def __init__(self,url):
        headers={}
        if os.environ.get('FOREST_OWNER_TOKEN'): headers['Authorization']='Bearer '+os.environ['FOREST_OWNER_TOKEN']
        self.client=httpx.Client(base_url=url.rstrip('/'),timeout=600,headers=headers)
    def __call__(self,method,path,**kwargs):
        response=self.client.request(method,path,**kwargs); response.raise_for_status(); return response.json()
    def read(self,pid,path): return self('GET',f'/api/projects/{pid}/file',params={'path':path})['content']
    def write(self,pid,path,content): return self('PUT',f'/api/projects/{pid}/file',json={'path':path,'content':content})
    def project(self,name,goal,paid=False):
        project=self('POST','/api/projects',json={'name':name,'goal':goal,'mode':'manual','budget':{'allow_paid':paid}})
        return project,self('GET',f'/api/projects/{project["id"]}/graph')
    def node(self,pid,branch,title,instructions,config):
        graph=self('GET',f'/api/projects/{pid}/graph'); nid=str(uuid.uuid4())
        self('POST',f'/api/projects/{pid}/graph/commands',json={'request_id':str(uuid.uuid4()),'expected_revision':graph['revision'],'operation':'add_node','params':{'id':nid,'branch_id':branch,'type':'implementation','title':title,'instructions':instructions,'config':config}})
        return nid
    def launch(self,nid,request_id): return self('POST',f'/api/nodes/{nid}/run',json={'request_id':request_id,'scope':'single'})
    def wait(self,rid,seconds=900,poll=2,on_observation=None,observation_interval=10):
        start=time.monotonic(); last_notified=None; last_status=None
        def observe(run,force=False):
            nonlocal last_notified,last_status
            now=time.monotonic()
            if on_observation and (force or last_notified is None or run['status']!=last_status or now-last_notified>=observation_interval):
                on_observation(run)
                last_notified=now; last_status=run['status']
        while time.monotonic()-start<seconds:
            run=self('GET',f'/api/runs/{rid}')
            observe(run,run['status'] in TERMINAL)
            if run['status'] in TERMINAL: return run
            time.sleep(poll)
        run=self('GET',f'/api/runs/{rid}'); observe(run,True); return run


def evidence(api,run,outputs):
    artifacts={}; errors=[]
    for name in outputs:
        try: artifacts[name]=api.read(run['project_id'],run['output_path']+'/workspace/'+name)
        except httpx.HTTPError as exc: errors.append(f'{name}: HTTP {getattr(exc.response,"status_code",None)}' if isinstance(exc,httpx.HTTPStatusError) else f'{name}: transport failure')
    session=api('GET',f'/api/runs/{run["id"]}/session')['session'] or {}
    usage=(api('GET',f'/api/runs/{run["id"]}/session',params={'summary':True}).get('session') or {}).get('usage')
    session={**session,'supervisory_usage':usage}
    receipts=[]
    for step in session.get('transcript',[]):
        result=step.get('tool_result',{})
        if result.get('process_id') and result.get('status')=='completed' and result.get('exit_code')==0: receipts.append(result['process_id'])
    receipts.extend(x['process_id'] for x in run.get('metrics',{}).get('executions',[]) if x.get('status')=='completed' and x.get('exit_code')==0)
    receipts.extend(run.get('metrics',{}).get('metrics_evidence',{}).get('successful_process_ids',[]))
    if not receipts: errors.append('No observed successful real-process receipt')
    return artifacts,session,sorted(set(receipts)),errors


def prime_oracle(artifacts):
    failures=[]
    try: metrics=json.loads(artifacts.get('metrics.json',''))
    except ValueError: return ['metrics.json is not valid JSON'],{}
    def numbers(value):
        if isinstance(value,dict): return sum((numbers(v) for v in value.values()),[])
        if isinstance(value,list): return sum((numbers(v) for v in value),[])
        return [value] if isinstance(value,(int,float)) and not isinstance(value,bool) else []
    expected=sum(all(n%d for d in range(2,math.isqrt(n)+1)) for n in range(2,10001))
    if numbers(metrics).count(expected)<2: failures.append(f'Metrics must preserve both actual counts equal to independently recomputed {expected}')
    if not artifacts.get('report.md','').strip(): failures.append('Missing report.md')
    functions=[]
    for path,source in artifacts.items():
        if not path.endswith('.py'): continue
        try: tree=ast.parse(source)
        except SyntaxError: failures.append(f'Invalid Python source: {path}'); continue
        for function in ast.walk(tree):
            if isinstance(function,(ast.FunctionDef,ast.AsyncFunctionDef)):
                functions.append({'source':path,'name':function.name,'remainder_operations':sum(isinstance(n,ast.Mod) for n in ast.walk(function)),'body':ast.dump(ast.Module(body=function.body,type_ignores=[])), 'indexed_writes':sum(isinstance(n,ast.Subscript) and isinstance(n.ctx,ast.Store) for n in ast.walk(function)), 'called_functions':[n.func.id for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]})
    trial=[f for f in functions if f['remainder_operations']>0]
    sieve=[f for f in functions if ('sieve' in f['name'].lower() or 'eratosthenes' in f['name'].lower()) and f['remainder_operations']==0 and f['indexed_writes']>0]
    if not any(a['body']!=b['body'] and a['name'] not in b['called_functions'] for a in trial for b in sieve): failures.append('Source inspection did not find distinct trial-division and sieve implementations')
    return failures,{'expected_count':expected,'functions':functions,'scope':'Structural source check and exact output oracle; preserved source remains available for human algorithm review.'}


def run_prime(api,args,record,persist):
    if not record.get('project_id'):
        project,_=api.project('Release qualification · original autonomous prime task',PRIME_GOAL,args.allow_paid)
        pid=project['id']; record.update(project_id=pid,started_at=utc(),status='running'); persist()
        api('PATCH',f'/api/projects/{pid}',json={'mode':'auto','config':{'provider_id':args.provider_id}})
        api('POST',f'/api/projects/{pid}/research/start',json={'autonomous':True}); persist()
    pid=record['project_id']
    current_project=api('GET',f'/api/projects/{pid}')
    if not current_project.get('config',{}).get('controller'):
        api('PATCH',f'/api/projects/{pid}',json={'mode':'auto','config':{'provider_id':args.provider_id}})
        api('POST',f'/api/projects/{pid}/research/start',json={'autonomous':True})
    started=time.monotonic()
    while time.monotonic()-started<args.task_timeout:
        project=api('GET',f'/api/projects/{pid}'); control=project.get('config',{}).get('controller',{})
        if control.get('status') in ('completed','blocked','budget_exhausted','stopped'): break
        time.sleep(args.poll)
    if control.get('status') not in ('completed','blocked','budget_exhausted','stopped'):
        record.update(status='running',monitor_timeout=True,controller=control); persist(); return
    runs=api('GET',f'/api/projects/{pid}/runs',params={'limit':500})
    completed_agents=[run for run in runs if run['kind']=='agent' and run['status']=='completed']
    source_paths={run['id']:[] for run in completed_agents}
    prefixes={run['output_path']+'/workspace/':run['id'] for run in completed_agents}
    cursor=None
    while True:
        params={'limit':1000}
        if cursor: params['cursor']=cursor
        page=api('GET',f'/api/projects/{pid}/files',params=params)
        for item in page['files']:
            if item['is_dir'] or not item['path'].endswith('.py'): continue
            for prefix,run_id in prefixes.items():
                if item['path'].startswith(prefix):
                    source_paths[run_id].append(item['path'][len(prefix):])
        if not page.get('has_more'): break
        cursor=page.get('next_cursor')
        if not cursor: raise RuntimeError('File listing reported more entries without a continuation cursor')
    failures=[]; checked=[]
    for run in completed_agents:
        sources=source_paths[run['id']]
        artifacts,session,receipts,errors=evidence(api,run,['metrics.json','report.md',*sources])
        oracle_errors,review=prime_oracle(artifacts); errors.extend(oracle_errors)
        checked.append({'run_id':run['id'],'errors':errors,'review':review,'receipts':receipts,'usage':(session.get('supervisory_usage') or {}).get('totals')})
        folder=Path(args.report).parent/'release-artifacts'/record['id']/run['id']
        for name,content in artifacts.items():
            path=folder/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content)
    if control.get('status')!='completed': failures.append('Controller did not conclude completed: '+str(control.get('status')))
    if not any(r['kind']=='research_plan' and r['status']=='completed' for r in runs): failures.append('No successful actual planner run')
    if not checked: failures.append('No completed Agent output to evaluate')
    for item in checked: failures.extend(item['errors'])
    record.update(status='passed' if not failures else 'failed',finished_at=utc(),failures=failures,controller=control,checked=checked,runs=[{'id':r['id'],'kind':r['kind'],'status':r['status'],'error':r.get('error'),'metrics':r.get('metrics',{})} for r in runs]); persist()


def evaluated_status(record):
    return record.get('latest_evaluation',{}).get('status',record.get('status'))


def agents(api,args,report):
    if not args.provider_id: raise ValueError('--provider-id is required; configure a real provider in the app first')
    selected=[t for t in corpus() if not args.task or t.id in args.task]
    if not selected: raise ValueError('No selected task IDs matched')
    providers=api('GET','/api/providers'); provider=next((p for p in providers if p['id']==args.provider_id),None)
    if not provider: raise ValueError('Provider not found')
    report['provider']={k:provider.get(k) for k in ('id','kind','model')}
    if args.allow_paid:
        usage=api('GET',f'/api/providers/{args.provider_id}/usage')
        if usage.get('limit_usd') is None or usage['limit_usd']>args.max_cost_usd: raise ValueError('Configure the server provider budget_usd at or below --max-cost-usd before paid qualification')
        if usage.get('remaining_usd') is None: raise ValueError('Provider remaining USD is unknown; priced usage is required for --allow-paid qualification')
        report['provider_usage_before']=usage
    report.setdefault('tasks',[]); report['status']='running'; report['latest_corpus_version']=VERSION
    def persist():
        if args.allow_paid: report['provider_usage_latest']=api('GET',f'/api/providers/{args.provider_id}/usage')
        report['updated_at']=utc(); save(args.report,report)
    for task in selected:
        previous=[r for r in report['tasks'] if r['id']==task.id]
        if previous and evaluated_status(previous[-1]) in ('passed','failed') and not args.retry_failed: continue
        if previous and evaluated_status(previous[-1])=='passed': continue
        record=previous[-1] if previous and previous[-1]['status']=='running' else {'id':task.id,'category':task.category,'attempt':len(previous)+1,'status':'running','started_at':utc(),'conditions':{'corpus_version':VERSION,'oracle_revision':ORACLE_REVISION,'provider_id':args.provider_id,'model':provider.get('model'),'context_char_budget':AGENT_CONTEXT_CHARS,'round_steps':args.max_steps,'task_timeout':args.task_timeout,'instructions':PRIME_GOAL if task.id=='01-prime-independence' else task_instruction(task),'output_contract':{'required_artifacts':['metrics.json','report.md'] if task.id=='01-prime-independence' else list(task.outputs),'metrics_required_keys':[] if task.id=='01-prime-independence' else list(task.expected.keys()),'top_level_types':{} if task.id=='01-prime-independence' else result_types(task.expected)}}}
        if record not in report['tasks']: report['tasks'].append(record)
        conditions=record.get('conditions',{})
        persist()
        if args.allow_paid:
            remaining=report['provider_usage_latest'].get('remaining_usd')
            if remaining is None or remaining<=0:
                reason='Provider remaining USD is unknown; spending capacity cannot be established' if remaining is None else 'Provider shared budget has no remaining reservation capacity'
                record.update(status='not_run_budget',failures=[reason]); persist(); break
        if task.id=='01-prime-independence':
            run_prime(api,args,record,persist)
            if record['status']=='running': return
        else:
            if not record.get('run_id'):
                if not record.get('node_id'):
                    project,graph=api.project('Release qualification · '+task.id,'Complete this bounded executable qualification task. Mathematical and parsing fixtures are program inputs. Prediction CSVs are actual prior execution outputs with provenance.',args.allow_paid)
                    pid=project['id']; branch=graph['branches'][0]
                    for name,content in task.inputs.items(): api.write(pid,branch['workspace']+'/'+name,content)
                    instruction=conditions.get('instructions') or task_instruction(task)
                    config={'kind':'agent','role':'Engineer' if task.category!='writing' else 'Analyst','provider_id':args.provider_id,'required_outputs':list(task.outputs),'metrics_file':'result.json','metrics_required_keys':list(task.expected.keys()),'instructions':instruction,'context_char_budget':AGENT_CONTEXT_CHARS,'agent_budget':{'active_seconds':args.task_timeout},**({'execution_round':{'steps':args.max_steps}} if args.max_steps is not None else {}),'resources':{'cpu':1,'memory_gb':.5}}
                    nid=api.node(pid,branch['id'],task.id,instruction,config)
                    record.update(project_id=pid,node_id=nid,request_id=str(uuid.uuid4())); persist()
                run=api.launch(record['node_id'],record['request_id']); record['run_id']=run['id']; persist()
            run=api.wait(record['run_id'],args.task_timeout,args.poll)
            if run['status'] not in TERMINAL:
                record.update(status='running',monitor_timeout=True); persist(); return
            artifacts,session,receipts,errors=evidence(api,run,task.outputs)
            errors.extend(check(task,artifacts,oracle_revision=conditions.get('oracle_revision',1)))
            if run['status']!='completed': errors.append('Agent run status: '+run['status'])
            folder=Path(args.report).parent/'release-artifacts'/task.id/run['id']
            for name,content in artifacts.items():
                path=folder/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content)
            record.update(status='passed' if not errors else 'failed',finished_at=utc(),run_status=run['status'],failures=errors,usage=(session.get('supervisory_usage') or {}).get('totals'),runtime_context_history=session.get('context_history',[]),runtime_context_budget_history=session.get('context_budget_history',[]),steps=len(session.get('transcript',[])),receipts=receipts,artifacts_directory=str(folder),run_error=run.get('error'),actual_configuration={k:run.get('config',{}).get(k) for k in ('provider_id','context_char_budget','execution_round','agent_budget','resources','instructions','required_outputs','metrics_file','metrics_required_keys','qualification_adjustments')},actual_processes=run.get('metrics',{}).get('executions',[])); persist()
        print(json.dumps({'task':task.id,'status':record['status'],'failures':record.get('failures',[])}),flush=True)
    latest={r['id']:r for r in report['tasks']}
    report['summary']={'selected':len(selected),'passed':sum(evaluated_status(latest.get(t.id,{}))=='passed' for t in selected),'failed':sum(evaluated_status(latest.get(t.id,{}))=='failed' for t in selected),'scope':'Selected bounded tasks; not a general intelligence, scientific novelty, publication, production, or soak certification'}
    report['status']='passed' if report['summary']['passed']==len(selected) else 'failed'; persist()



def rescore(api,args,report):
    tasks={t.id:t for t in corpus()}; rescored=[]
    for record in report.get('tasks',[]):
        task=tasks.get(record.get('id'))
        if record.get('status') not in ('passed', 'failed'): continue
        if not task or task.id=='01-prime-independence' or (args.task and task.id not in args.task) or not record.get('run_id'): continue
        run=api('GET',f'/api/runs/{record["run_id"]}')
        if run['status']!='completed': continue
        artifacts,session,receipts,errors=evidence(api,run,task.outputs)
        errors.extend(check(task,artifacts))
        history=record.setdefault('evaluation_history',[])
        if not history:
            history.append({'kind':'original_evaluation','evaluated_at':record.get('finished_at'),'oracle_revision':record.get('conditions',{}).get('oracle_revision',1),'status':record['status'],'failures':list(record.get('failures',[])),'run_id':record['run_id']})
        evaluation={'kind':'read_only_rescore','evaluated_at':utc(),'oracle_revision':ORACLE_REVISION,'original_corpus_version':record.get('conditions',{}).get('corpus_version',report.get('corpus_version')),'status':'passed' if not errors else 'failed','failures':errors,'run_id':run['id'],'run_status':run['status'],'successful_process_receipts':receipts,'numeric_reference_changed':False,'model_outputs_modified':False,'new_model_calls':0,'source':'Existing completed run artifacts read through the actual API'}
        history.append(evaluation); record['latest_evaluation']=evaluation; rescored.append({'id':record['id'],'run_id':run['id'],'original_status':record['status'],'rescored_status':evaluation['status'],'failures':errors})
        save(args.report,report)
    latest={r['id']:r for r in report.get('tasks',[])}
    report['rescored_summary']={'oracle_revision':ORACLE_REVISION,'unique_tasks_present':len(latest),'passed':sum(evaluated_status(r)=='passed' for r in latest.values()),'failed':sum(evaluated_status(r)=='failed' for r in latest.values()),'original_summary_preserved':True,'new_model_calls':0,'evaluated_at':utc()}
    report.setdefault('rescore_operations',[]).append({'at':utc(),'oracle_revision':ORACLE_REVISION,'records':rescored})
    save(args.report,report)
    print(json.dumps({'rescored':rescored,'summary':report['rescored_summary']},indent=2),flush=True)

def graph(api,args,report):
    start=time.monotonic(); project,g=api.project('Release qualification · graph scale','Exercise real graph storage, paging, and edit APIs. This graph-scale test does not execute 1,000 computations.')
    pid=project['id']; branch=g['branches'][0]; ids=[str(uuid.uuid4()) for _ in range(args.nodes)]
    commands=[{'operation':'add_node','params':{'id':nid,'branch_id':branch['id'],'type':'idea','title':f'Qualification node {i:04}','instructions':'Graph storage qualification; no computation was executed.','position':{'x':(i%20)*250,'y':(i//20)*140},**({'parent_id':ids[(i-1)//2]} if i else {})}} for i,nid in enumerate(ids)]
    revision=g['revision']; batches=0
    for offset in range(0,len(commands),200):
        created=api('POST',f'/api/projects/{pid}/graph/batch',json={'request_id':str(uuid.uuid4()),'expected_revision':revision,'commands':commands[offset:offset+200]})
        revision=created['revision']; batches+=1
    write_seconds=time.monotonic()-start
    page_start=time.monotonic(); observed=[]; offset=0; pages=0
    while True:
        page=api('GET',f'/api/projects/{pid}/graph/page',params={'offset':offset,'limit':100}); observed.extend(n['id'] for n in page['nodes']); pages+=1
        if page['next_offset'] is None: break
        offset=page['next_offset']
    paging_seconds=time.monotonic()-page_start
    api('PATCH',f'/api/nodes/{ids[-1]}',json={'title':'Edited final qualification node'})
    final=api('GET',f'/api/projects/{pid}/graph'); node=api('GET',f'/api/nodes/{ids[-1]}')
    failures=[]
    if len(observed)!=args.nodes or set(observed)!=set(ids): failures.append('Paged graph did not return each submitted node exactly once')
    if len(final['edges'])!=args.nodes-1: failures.append('Tree edge count differs from nodes minus one')
    if node['title']!='Edited final qualification node': failures.append('Ordinary node edit did not persist')
    report.update(status='passed' if not failures else 'failed',project_id=pid,node_count=len(final['nodes']),edge_count=len(final['edges']),batches=batches,pages=pages,creation_seconds=write_seconds,paging_seconds=paging_seconds,observed_seconds=time.monotonic()-start,failures=failures,executed_compute_nodes=0,scope='API graph storage, pagination and ordinary editing only; no parallel compute or browser frame-rate claim'); save(args.report,report)


def compute(api,args,report):
    if not report.get('run_id'):
        project,g=api.project('Release qualification · continuous real compute','Run repeated real minibatch softmax training and retain predictions. This is runtime endurance, not a novel ML result.')
        pid=project['id']; branch=g['branches'][0]
        api.write(pid,branch['workspace']+'/train_digits.py',(ROOT/'scripts/qualify_task.py').read_text())
        cfg={'kind':'experiment','command':[sys.executable,'train_digits.py','--seed','17','--epochs','8','--seconds',str(args.seconds)],'metrics_file':'metrics.json','resources':{'cpu':1,'memory_gb':.5},'recovery':{'mode':'checkpoint','checkpoint':'state.json','max_attempts':5}}
        nid=api.node(pid,branch['id'],'Real digits softmax endurance','Execute actual training for the requested duration; preserve actual timing and predictions.',cfg)
        run=api.launch(nid,str(uuid.uuid4())); report.update(project_id=pid,node_id=nid,run_id=run['id'],requested_seconds=args.seconds,status='running'); save(args.report,report)
    if args.submit_only: return
    def observed_run(run):
        # This callback follows an actual API observation. It does not infer
        # compute time, completion or replacement work from monitor uptime.
        archive_monitor_error(report)
        observed_at=utc()
        report.update(status='running',run_status=run['status'],updated_at=observed_at,
                      last_run_observation={'run_id':run['id'],'status':run['status'],'observed_at':observed_at})
        save(args.report,report)
    run=api.wait(report['run_id'],args.seconds+600,args.poll,on_observation=observed_run)
    report['run_status']=run['status']
    if run['status'] not in TERMINAL: save(args.report,report); return
    errors=[]
    if run['status']!='completed': errors.append('Compute run did not complete')
    try:
        metrics=json.loads(api.read(report['project_id'],run['output_path']+'/workspace/metrics.json'))
        prediction_text=api.read(report['project_id'],run['output_path']+'/workspace/predictions.csv')
        rows=list(csv.DictReader(io.StringIO(prediction_text)))
        correct=0; losses=[]
        for row in rows:
            label=int(float(row['label'])); probs=[float(row['p'+str(i)]) for i in range(10)]
            correct+=max(range(10),key=probs.__getitem__)==label; losses.append(-math.log(probs[label]))
        accuracy=correct/len(rows); loss=sum(losses)/len(rows)
        if len(rows)!=metrics['n_test'] or not math.isclose(accuracy,metrics['accuracy'],abs_tol=1e-12) or not math.isclose(loss,metrics['log_loss'],rel_tol=1e-10): errors.append('Independent prediction recomputation differs from reported metrics')
        if metrics['elapsed_seconds']<report['requested_seconds']: errors.append('Observed compute duration below requested duration')
        report.update(metrics=metrics,independent_accuracy=accuracy,independent_log_loss=loss,observed_compute_seconds=metrics['elapsed_seconds'],attempts=run.get('resource',{}).get('attempts',[]))
    except (httpx.HTTPError,ValueError,KeyError,ZeroDivisionError) as exc: errors.append('Cannot verify outputs: '+str(exc))
    report.update(status='passed' if not errors else 'failed',finished_at=utc(),failures=errors,run_error=run.get('error')); save(args.report,report)


def soak(api,args,report):
    report.setdefault('checks',[]); report.setdefault('segments',[]); report.setdefault('observed_seconds',0); report.setdefault('requested_seconds',args.seconds)
    if not report.get('project_id'):
        project,g=api.project('Release qualification · service soak','Observe actual API availability, durable writes and repeated real computations over time.')
        report.update(project_id=project['id'],branch_id=g['branches'][0]['id'],workspace=g['branches'][0]['workspace'])
        save(args.report,report)
    if not report.get('node_id'):
        script='import json\nfrom pathlib import Path\nn=100000\nresult=sum(i*i for i in range(n))\nPath("metrics.json").write_text(json.dumps({"n":n,"sum_squares":result}))\n'
        api.write(report['project_id'],report['workspace']+'/soak_compute.py',script)
        report['node_id']=api.node(report['project_id'],report['branch_id'],'Periodic real arithmetic','Compute exact integer sum of squares.',{'kind':'command','command':[sys.executable,'soak_compute.py'],'metrics_file':'metrics.json','resources':{'cpu':1,'memory_gb':.2}})
        save(args.report,report)
    start=time.monotonic(); prior=report['observed_seconds']; segment={'started_at':utc(),'observed_seconds':0}; report['segments'].append(segment)
    last_probe=time.monotonic(); last_wall=time.time()
    while report['observed_seconds']<args.seconds or report.get('pending_check'):
        now=time.monotonic(); wall=time.time(); limit=max(2*args.interval,120)
        if wall-last_wall>limit:
            gap=max(0,now-last_probe); start+=gap
            report.setdefault('monitoring_gaps',[]).append({'at':utc(),'wall_gap_seconds':wall-last_wall,'excluded_monotonic_seconds':gap})
        last_probe=now; last_wall=wall
        if not report.get('pending_check'):
            report['pending_check']={'request_id':str(uuid.uuid4()),'started_at':utc()}
            save(args.report,report)
        pending=report['pending_check']; entry={'at':utc(),'request_id':pending['request_id']}; tick=time.monotonic()
        try:
            api('GET','/api/health'); value=utc(); path=report['workspace']+'/soak_check.txt'; api.write(report['project_id'],path,value)
            if api.read(report['project_id'],path)!=value: raise ValueError('Read-after-write mismatch')
            if not pending.get('run_id'):
                run=api.launch(report['node_id'],pending['request_id']); pending['run_id']=run['id']; save(args.report,report)
            run=api.wait(pending['run_id'],max(60,args.interval),args.poll); entry['run_id']=run['id']
            if run['status'] not in TERMINAL:
                entry.update(status='pending',run_status=run['status'])
            else:
                report.pop('pending_check',None)
                if run['status']!='completed' or run.get('metrics',{}).get('sum_squares')!=99999*100000*199999//6:
                    raise ValueError('Real arithmetic run failed external exact oracle')
                entry.update(status='passed')
        except (httpx.HTTPError,ValueError) as exc:
            entry.update(status='failed',error=str(exc))
        entry['request_seconds']=time.monotonic()-tick
        if entry['request_seconds']>limit:
            start+=entry['request_seconds']; last_probe=time.monotonic(); last_wall=time.time()
            report.setdefault('monitoring_gaps',[]).append({'at':utc(),'during_check':True,'excluded_monotonic_seconds':entry['request_seconds']})
        report['checks'].append(entry)
        segment['observed_seconds']=time.monotonic()-start; report.update(observed_seconds=prior+segment['observed_seconds'],status='running',requested_seconds=args.seconds,updated_at=utc()); save(args.report,report)
        remaining=args.seconds-report['observed_seconds']
        if remaining>0 or report.get('pending_check'): time.sleep(min(args.interval,max(1,remaining) if remaining>0 else args.interval,60))
    segment.update(finished_at=utc(),observed_seconds=time.monotonic()-start)
    failures=[c for c in report['checks'] if c['status']=='failed']
    report.update(observed_seconds=prior+segment['observed_seconds'],finished_at=utc(),status='passed' if not failures else 'failed',scope='Sampled availability, writes and real worker runs over the observed monitoring segments; gaps between segments are excluded. This is not continuous CPU computation.'); save(args.report,report)

def brief_report(report, path):
    """Console summary only; the saved JSON retains complete provider receipts."""
    result = {key: report[key] for key in ('mode', 'status', 'summary',
              'requested_seconds', 'observed_seconds', 'observed_compute_seconds', 'node_count',
              'edge_count', 'creation_seconds', 'paging_seconds', 'error') if key in report}
    result['report'] = str(path)
    usage = report.get('provider_usage_latest')
    if isinstance(usage, dict):
        result['shared_provider_cost_usd'] = {key: usage[key] for key in
                ('limit_usd', 'estimated_cost_usd', 'reserved_usd', 'remaining_usd') if key in usage}
    return result


def archive_monitor_error(report):
    """Start a new monitor invocation without presenting an old error as current."""
    if 'error' not in report:
        return False
    recorded_at = report.get('error_recorded_at', report.get('updated_at'))
    report.setdefault('monitor_error_history', []).append({
        'error': report.pop('error'), 'recorded_at': recorded_at,
        'recorded_at_basis': 'error_recorded_at' if 'error_recorded_at' in report else 'legacy_updated_at',
        'status_at_archive': report.get('status'), 'archived_at': utc()})
    report.pop('error_recorded_at', None)
    return True


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['agents','graph','compute','soak','list','rescore'])
    parser.add_argument('--url',default='http://127.0.0.1:8000'); parser.add_argument('--report'); parser.add_argument('--resume',action='store_true')
    parser.add_argument('--provider-id'); parser.add_argument('--allow-paid',action='store_true'); parser.add_argument('--max-cost-usd',type=float,default=5)
    parser.add_argument('--task',action='append'); parser.add_argument('--retry-failed',action='store_true'); parser.add_argument('--round-steps','--max-steps',dest='max_steps',type=int,default=None,help='Technical work-round size hint; automatically continues checkpointed rounds'); parser.add_argument('--task-timeout',type=float,default=900)
    parser.add_argument('--nodes',type=int,default=1000); parser.add_argument('--seconds',type=float); parser.add_argument('--interval',type=float,default=60); parser.add_argument('--poll',type=float,default=2); parser.add_argument('--submit-only',action='store_true')
    args=parser.parse_args(argv)
    if args.mode=='list':
        print(json.dumps([{'id':t.id,'category':t.category,'prompt':PRIME_GOAL if t.id=='01-prime-independence' else t.prompt} for t in corpus()],indent=2)); return 0
    args.report=args.report or str(ROOT/'var/qa'/('release-'+args.mode+'.json'))
    args.seconds=args.seconds if args.seconds is not None else (14400 if args.mode=='compute' else 86400)
    if args.max_steps is not None and args.max_steps<=0: parser.error('--round-steps must be positive when explicitly supplied')
    if args.nodes<1 or args.seconds<=0 or args.poll<=0 or args.interval<=0: parser.error('Durations, node count and poll interval must be positive')
    path=Path(args.report)
    with report_lock(path):
        if args.mode=='rescore' and not path.exists(): parser.error('Rescore requires an existing Agent report via --report')
        if path.exists() and not args.resume and args.mode!='rescore': parser.error('Report already exists; choose a new --report or use --resume')
        report=json.loads(path.read_text()) if path.exists() else {'schema_version':1,'corpus_version':VERSION,'mode':args.mode,'started_at':utc(),'url':args.url,'status':'running'}
        if report['mode']!=('agents' if args.mode=='rescore' else args.mode) or report['url']!=args.url: parser.error('Resume requires the original mode and API URL')
        if archive_monitor_error(report): save(path,report)
        if report.get('status') in ('passed','failed') and args.mode in ('compute','soak'):
            print(json.dumps({'status':report['status'],'report':str(path),'monitor':'already finished'})); return 0 if report['status']=='passed' else 1
        signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        api=API(args.url)
        try: globals()[args.mode](api,args,report)
        except KeyboardInterrupt:
            if report.get('status') in ('passed','failed'): return 0 if report['status']=='passed' else 1
            report.update(status='interrupted_monitor',updated_at=utc(),note='Monitor stopped. Durable server work may continue; resume this report to inspect it.'); save(path,report); return 130
        except Exception as exc:
            recorded_at=utc()
            report.update(status='error',updated_at=recorded_at,error_recorded_at=recorded_at,error=str(exc)); save(path,report); raise
        finally: api.client.close()
        if args.mode=='rescore': return 0
        print(json.dumps(brief_report(report,path),indent=2))
        return 0 if report['status'] in ('passed','running') else 1

if __name__=='__main__': raise SystemExit(main())
