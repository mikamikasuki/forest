import ast
import io
import json
import re
import shutil
import zipfile
from pathlib import Path
from fastapi import APIRouter, Body, UploadFile, File
from fastapi.responses import Response, FileResponse
from sqlalchemy import select
from .common import *
from .schemas import ResourceCreate
from services.worker.scheduler import enqueue
from .paper_state import locked_project_paper, locked_paper, write_working_source, apply_generation_review
router=APIRouter()
@router.post('/api/library/search')
def search_library(body:dict=Body(...)):
    from research.literature.sources import search
    try: result=search(body['query'],body.get('source','crossref'),min(int(body.get('limit',8)),30)); return {'results':result}
    except Exception as exc: error('LITERATURE_SEARCH_FAILED',str(exc),502,'Try the other source or import a DOI.',True)
@router.post('/api/library/import')
def import_library(body:dict=Body(...)):
    from research.literature.sources import import_identifier,extract_pdf
    pid=body['project_id']; folder=project_dir(pid)/'library'; folder.mkdir(parents=True,exist_ok=True)
    if body.get('paper'): paper=body['paper']
    elif body.get('identifier'):
        try: paper=import_identifier(body['identifier'],folder)
        except Exception as exc: error('IMPORT_FAILED',str(exc),422)
    else: error('MISSING_IDENTIFIER','Provide DOI, arXiv URL, public PDF URL or paper metadata')
    if isinstance(paper,list): rows=paper
    else: rows=[paper]
    saved=[]
    with Session.begin() as s:
        get(s,Project,pid)
        for paper in rows:
            # Stable identifiers take precedence; title fallback is for records
            # where neither side has a DOI or arXiv identity.
            def base_arxiv(value):
                return re.sub(r'v\d+$','',value) if isinstance(value,str) and value else None
            paper_doi=paper.get('doi')
            paper_arxiv=base_arxiv(paper.get('arxiv_id'))
            old=next((r for r in s.scalars(select(SourcePaper).where(SourcePaper.project_id==pid))
                      if ((paper_doi and paper_doi==r.data.get('doi'))
                          or (paper_arxiv and paper_arxiv==base_arxiv(r.data.get('arxiv_id')))
                          or (not paper_doi and not paper_arxiv
                              and not r.data.get('doi') and not r.data.get('arxiv_id')
                              and paper.get('title','').strip().lower()==r.title.strip().lower()))),None)
            if old: saved.append(asdict(old)); continue
            r=SourcePaper(project_id=pid,title=paper.get('title','Imported paper'),data=paper,status='available'); s.add(r); s.flush()
            for passage in paper.get('passages',[]): s.add(SourcePassage(project_id=pid,title=r.title,data={'paper_id':r.id,**passage},status='available'))
            saved.append(asdict(r)); emit(s,pid,'artifact_available',{'kind':'library','id':r.id})
    return saved[0] if len(saved)==1 else {'records':saved}
@router.get('/api/library/export-bibtex')
def export_bibtex(project_id:str):
    from research.literature.sources import bibtex
    with Session() as s: records=[{**r.data,'title':r.title} for r in s.scalars(select(SourcePaper).where(SourcePaper.project_id==project_id))]
    return Response(bibtex(records),media_type='application/x-bibtex',headers={'Content-Disposition':'attachment; filename="references.bib"'})
@router.get('/api/library/{ident}/passages')
def passages(ident:str):
    with Session() as s:
        r=get(s,SourcePaper,ident); existing=[asdict(x) for x in s.scalars(select(SourcePassage).where(SourcePassage.project_id==r.project_id)) if x.data.get('paper_id')==ident]
        return existing or r.data.get('passages',[])
@router.post('/api/library/compare')
def compare_papers(body:dict=Body(...)):
    with Session() as s:
        records=[get(s,SourcePaper,ident) for ident in body['ids']]
        if len({r.project_id for r in records})>1: error('CROSS_PROJECT','Sources must belong to one project')
        return {'papers':[{'id':r.id,'title':r.title,'abstract':r.data.get('abstract',''),'authors':r.data.get('authors',[]),'year':r.data.get('year'),'reading_scope':r.data.get('reading_scope','metadata/abstract'),'notes':r.data.get('notes',''),'evidence_label':'REPORTED'} for r in records]}
@router.post('/api/library/{ident}/reindex')
def reindex(ident:str):
    from research.literature.sources import extract_pdf
    with Session() as s:
        r=get(s,SourcePaper,ident); pid=r.project_id; revision=r.revision
        path=r.data.get('pdf_path') or r.data.get('fulltext_path')
        if not path: error('FULLTEXT_UNAVAILABLE','Import a public PDF or upload one first',409)
        p=safe_path(project_dir(pid),path,True)
    passages=extract_pdf(p)
    if isinstance(passages,dict): passages=passages.get('passages',[])
    with Session.begin() as s:
        from services.worker.scheduler import _lock_project
        _lock_project(s,pid);r=get(s,SourcePaper,ident,for_update=True)
        if r.revision!=revision:error('REVISION_CONFLICT','Source changed during extraction; reindex the current source',409)
        # Keep the normalized passage rows in sync with the source snapshot. The
        # passages endpoint prefers these rows over data.passages, so updating
        # only the JSON field leaves stale text visible after a reindex.
        for passage in s.scalars(select(SourcePassage).where(SourcePassage.project_id==r.project_id)):
            if passage.data.get('paper_id')==ident:
                s.delete(passage)
        for passage in passages:
            s.add(SourcePassage(project_id=r.project_id,title=r.title,
                                data={'paper_id':r.id,**passage},status='available'))
        r.data={**r.data,'passages':passages,'reading_scope':'fulltext'}; r.revision+=1; touch_dependents(s,r.project_id,ident); return asdict(r)
@router.post('/api/research/ideas')
def generate_ideas(body:dict=Body(...)):
    with Session.begin() as s: return asdict(enqueue(s,body['project_id'],'ideas',body,body.get('request_id')))
@router.post('/api/research/suggest-paths')
def suggest_paths(body:dict=Body(...)):
    scope=body.get('scope','node')
    if scope not in ('node','branch','project'): error('INVALID_SCOPE','Scope must be node, branch, or project',422)
    with Session.begin() as s:
        project=get(s,Project,body['project_id'])
        node=get(s,Node,body['node_id']) if body.get('node_id') else None
        if node and node.project_id!=project.id: error('CROSS_PROJECT','Selected node belongs to another project',422)
        if scope in ('node','branch') and not node: error('NODE_REQUIRED','Select a node to choose node or branch scope',422)
        return asdict(enqueue(s,project.id,'suggest_paths',body,body.get('request_id'),node))
@router.post('/api/ideas/{ident}/adopt')
def adopt(ident:str,body:dict=Body(default={})):
    from copy import deepcopy
    from research.kernel import GraphCommandService
    from services.worker.scheduler import _lock_project
    with Session.begin() as s:
        idea=get(s,Hypothesis,ident)
        p=_lock_project(s,idea.project_id)
        s.refresh(idea)
        g=graph_from_db(s,p)
        request_id=body.get('request_id')
        if request_id is not None:
            if not isinstance(request_id,str) or not request_id or len(request_id)>120:
                error('INVALID_REQUEST_ID','Use a nonempty request_id of at most 120 characters',422)
            receipt_key='adopt:'+request_id
            previous=s.scalar(select(CommandReceipt).where(CommandReceipt.project_id==p.id,CommandReceipt.request_id==receipt_key))
            if previous:
                if previous.response.get('idea_id')!=idea.id: error('REQUEST_ID_CONFLICT','This request_id belongs to another idea',409)
                g.pop('_history',None)
                return g
        if body.get('expected_revision',p.revision)!=p.revision:
            error('REVISION_CONFLICT','The project changed; refresh before adopting the idea',409)
        if any(node.get('adopted_idea_id')==idea.id for node in g['nodes']):
            error('IDEA_ALREADY_ADOPTED','This idea has already been adopted; use its existing execution path',409)
        original=deepcopy(g); commands=[]
        config=deepcopy(body.get('config',idea.data.get('experiment_config',{})))
        if not isinstance(config,dict): error('INVALID_EXPERIMENT_CONFIG','Experiment config must be an object',422)
        inputs=deepcopy(body.get('inputs',config.pop('inputs',idea.data.get('inputs',[]))))
        if not isinstance(inputs,list) or len(inputs)>50: error('INVALID_INPUTS','Supply at most 50 explicit input references',422)
        models={'idea':Hypothesis,'paper':SourcePaper,'dataset':DatasetAsset,'run':TaskRun,'figure':Figure,'analysis':Analysis}
        for ref in inputs:
            if isinstance(ref,str): ref={'path':ref}
            if not isinstance(ref,dict): error('INVALID_INPUTS','Inputs must be paths or reference objects',422)
            if ref.get('project_id') not in (None,p.id): error('CROSS_PROJECT','Inputs must belong to this project',422)
            if ref.get('node_id'):
                source=get(s,Node,ref['node_id'])
                if source.project_id!=p.id: error('CROSS_PROJECT','Input node belongs to another project',422)
            if ref.get('kind') in models and ref.get('id'):
                source=get(s,models[ref['kind']],ref['id'])
                if source.project_id!=p.id: error('CROSS_PROJECT','Input record belongs to another project',422)
            if ref.get('path'):
                if not isinstance(ref['path'],str): error('INVALID_INPUTS','Input paths must be strings',422)
                safe_path(project_dir(p.id),ref['path'])
        branch_id=body.get('branch_id') or next((b['id'] for b in g['branches'] if b.get('is_main')),None)
        branch=next((b for b in g['branches'] if b['id']==branch_id),None)
        if not branch: error('MISSING_BRANCH','Choose an existing project branch',422)
        if branch.get('status','active')!='active': error('BRANCH_INACTIVE','Restore this branch before adopting an idea',409)
        title=body.get('title',idea.title)
        if not isinstance(title,str) or not title.strip() or len(title)>350: error('INVALID_TITLE','Use a title of 1–350 characters',422)
        source_text=json.dumps(idea.data,ensure_ascii=False,indent=2)
        if isinstance(idea.data.get('supporting_source_ids'),list):
            provenance_note='Source roles were selected by a model from retrieved metadata/abstracts; verify each source before citing. Background sources are not direct support.'
        else:
            provenance_note='This older idea has an unclassified retrieved-source pool. Do not treat its source list as evidence for this direction without checking each item.'
        custom=body.get('instructions','')
        if not isinstance(custom,str): error('INVALID_INSTRUCTIONS','Instructions must be text',422)
        source_input={'kind':'idea','id':idea.id,'project_id':p.id,'revision':idea.revision,
            'snapshot':{'id':idea.id,'title':idea.title,'revision':idea.revision,
                        'status':idea.status,'data':idea.data}}
        common_inputs=[source_input,*inputs]
        duty=('State the falsifiable claim, strongest baseline, decisive unknown, primary metric, meaningful effect threshold, '
              'matched compute/data budget, split/seed policy, mechanism ablation and stopping rule. Choose the cheapest decisive '
              'experiment. Separate MEASURED, REPORTED and ESTIMATED statements; never invent data, code availability or results.')
        hypothesis_id,engineer_id,experiment_id=uid(),uid(),uid()
        x=max((n.get('position',{}).get('x',0) for n in g['nodes'] if n['branch_id']==branch_id),default=-300)+300
        def add(nid,node_type,label,instructions,node_config,node_inputs,column,parent=None):
            nonlocal g
            params={'id':nid,'branch_id':branch_id,'type':node_type,'title':label,'instructions':instructions,
                    'config':node_config,'inputs':node_inputs,'position':{'x':x+column*320,'y':80},'adopted_idea_id':idea.id}
            if parent: params['parent_id']=parent
            commands.append({'operation':'add_node','targets':[],'params':params})
        add(hypothesis_id,'hypothesis',title,
            'Develop this adopted idea into a testable protocol. '+duty+' Write hypothesis.md in the run workspace and finish with that actual artifact.\n'
            +'Owner instructions: '+custom+'\n'+provenance_note+'\nIdea source material (claims are untested unless supported):\n'+source_text,
            {'kind':'agent','role':'Researcher'},common_inputs,0)
        protocol={'node_id':hypothesis_id,'path':'hypothesis.md','destination':'hypothesis.md','branch_id':branch_id}
        command=config.get('command')
        valid_command=(isinstance(command,str) and bool(command.strip())) or (isinstance(command,list) and bool(command) and all(isinstance(v,str) and bool(v) for v in command))
        entrypoint=config.get('entrypoint')
        valid_entrypoint=isinstance(entrypoint,str) and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*',entrypoint) is not None
        explicit_case=config.get('case')=='class_weight_correction'
        if explicit_case: config['example']='class_weight_calibration'
        if explicit_case and not (valid_command or valid_entrypoint):
            from research.experiments.case import configuration,DEFAULT_CONFIG
            try: configuration({k:v for k,v in config.items() if k in DEFAULT_CONFIG})
            except (ValueError,TypeError,KeyError) as exc: error('INVALID_EXPERIMENT_CONFIG',str(exc),422)
        if valid_command or valid_entrypoint or explicit_case:
            # An explicit executable config is retained. Empty configs must never
            # accidentally select the executor's bundled demonstration case.
            if not valid_command: config.pop('command',None)
            if not valid_entrypoint: config.pop('entrypoint',None)
            config['kind']='command' if valid_command and config.get('kind')=='command' else 'experiment'
            add(experiment_id,'experiment','Experiment · '+title,
                'Execute the supplied experiment configuration against hypothesis.md. '+duty+' Preserve actual predictions, metrics and failures.\n'+source_text,
                config,[*common_inputs,protocol],1,hypothesis_id)
            adopted_ids=[hypothesis_id,experiment_id]
        else:
            proposed=json.dumps(config,ensure_ascii=False,indent=2)
            add(engineer_id,'implementation','Implementation · '+title,
                'Read hypothesis.md and implement the full submission experiment matrix using the supplied real inputs. '+duty+
                ' Write self-contained implementation.py and experiment_plan.json in the run workspace. The plan must specify '
                'the exact Python argv, required real datasets and splits, strong recent baselines, component ablations, independent seeds, metrics, statistical units, and runtime/resources. Benchmark the scale against at least fifteen related accepted papers; keep a pilot separate from the full confirmation matrix. implementation.py '
                'must write metrics.json and preserve predictions when run. Do syntax/small smoke validation only; the downstream '
                'Experimenter owns the full measured run. Do not substitute the bundled classification case for this idea. '
                'If required data or access is missing, identify the exact missing input and fail explicitly. Finish with both actual files.\n'
                +'Unvalidated proposed configuration (implement or correct explicitly):\n'+proposed,
                {'kind':'agent','role':'Engineer'},[*common_inputs,protocol],1,hypothesis_id)
            generated=[{'node_id':engineer_id,'path':name,'destination':name,'branch_id':branch_id} for name in ('implementation.py','experiment_plan.json')]
            add(experiment_id,'experiment','Discriminating Experiment · '+title,
                'Read hypothesis.md, experiment_plan.json and implementation.py. Verify the planned inputs and execute the full '
                'submission experiment matrix with run_command using the supplied Python script, within this task budget. '+duty+
                ' Execute strong baselines, mechanism ablations and scenario/stress studies across the real datasets and independent seeds, preserve raw predictions and measured metrics.json, '
                'and write experiment_report.md with exact commands, actual results and the evidence-supported conclusion. '
                'If an input or implementation is missing, fail with that concrete reason. Do not claim an experiment ran without '
                'tool evidence. Finish with the actual metrics and report artifacts.',
                {'kind':'agent','role':'Experimenter','requires_implementation':True},
                [*common_inputs,protocol,*generated],2,engineer_id)
            adopted_ids=[hypothesis_id,engineer_id,experiment_id]
        from services.interventions.application import apply_in_session
        graph_request='adopt:'+(request_id or uid())
        applied=apply_in_session(s,p,graph_request,original['revision'],commands,
            actor='owner',batch=True,origin={'idea_id':idea.id},single_revision=True)
        g=applied['graph']
        idea.status='adopted'
        idea.data={**idea.data,'adoption':{'node_ids':adopted_ids,'branch_id':branch_id,'graph_revision':g['revision']}}
        receipt=s.scalar(select(CommandReceipt).where(CommandReceipt.project_id==p.id,CommandReceipt.request_id==graph_request))
        receipt.response={**receipt.response,'idea_id':idea.id,'node_ids':adopted_ids}
        emit(s,p.id,'node_changed',{'adopted_idea_id':idea.id,'node_ids':adopted_ids})
        g=graph_from_db(s,p)
        g.pop('_history',None)
        return g
@router.post('/api/theory/check')
def theory_check(body:dict=Body(...)):
    # A restricted expression grammar, not eval/sympify of arbitrary user code.
    import sympy as sp
    expression=body.get('expression',''); variable=body.get('variable','x')
    if len(expression)>1000 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*',variable): error('INVALID_EXPRESSION','Expression or variable is invalid')
    symbols={n:sp.Symbol(n) for n in set(re.findall(r'\b[A-Za-z][A-Za-z0-9]*\b',expression))}
    funcs={'sin':sp.sin,'cos':sp.cos,'exp':sp.exp,'log':sp.log,'sqrt':sp.sqrt,'abs':sp.Abs}
    def convert(n):
        if isinstance(n,ast.Constant) and isinstance(n.value,(int,float)) and not isinstance(n.value,bool):
            if abs(n.value)>1e6: raise ValueError('Numeric constant too large')
            return sp.sympify(n.value)
        if isinstance(n,ast.Name): return symbols.get(n.id,sp.Symbol(n.id))
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)): return convert(n.operand) if isinstance(n.op,ast.UAdd) else -convert(n.operand)
        if isinstance(n,ast.BinOp):
            a,b=convert(n.left),convert(n.right)
            if isinstance(n.op,ast.Add): return a+b
            if isinstance(n.op,ast.Sub): return a-b
            if isinstance(n.op,ast.Mult): return a*b
            if isinstance(n.op,ast.Div): return a/b
            if isinstance(n.op,ast.Pow) and (not b.is_number or abs(float(b))<=20): return a**b
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in funcs and len(n.args)==1 and not n.keywords: return funcs[n.func.id](convert(n.args[0]))
        raise ValueError('Use arithmetic, variables and sin/cos/exp/log/sqrt only')
    try:
        expr=convert(ast.parse(expression.replace('^','**'),mode='eval').body); x=sp.Symbol(variable); kind=body.get('kind','simplify')
        if kind=='differentiate': result=sp.diff(expr,x)
        elif kind=='solve': result=sp.solve(expr,x)
        elif kind=='numeric': result=expr.evalf(subs={sp.Symbol(k):float(v) for k,v in body.get('values',{}).items()})
        else: result=sp.simplify(expr)
        # A symbolic derivation from the supplied expression, not an empirical observation.
        data={'expression':expression,'kind':kind,'result':str(result),'latex':sp.latex(result),'evidence_label':'INFERRED','check_type':'symbolic_computation','scope':'This computation checks the supplied expression; it is not a proof of unprovided assumptions.'}
    except Exception as exc: error('EXPRESSION_FAILED',str(exc),422)
    with Session.begin() as s:
        get(s,Project,body['project_id']); r=Derivation(project_id=body['project_id'],title=body.get('title',expression),data=data,status='checked'); s.add(r); s.flush(); emit(s,r.project_id,'tool_finished',{'kind':'theory','id':r.id}); return asdict(r)
@router.post('/api/reviews')
def review(body:dict=Body(...)):
    with Session.begin() as s: return asdict(enqueue(s,body['project_id'],'review',body,body.get('request_id')))
@router.post('/api/experiments/{ident}/launch')
def launch(ident:str,body:dict=Body(default={})):
    with Session.begin() as s:
        e=get(s,ExperimentSpec,ident); n=s.get(Node,e.data.get('node_id')) if e.data.get('node_id') else None
        return asdict(enqueue(s,e.project_id,'experiment',{**e.data,**body,'experiment_id':ident,'experiment_revision':e.revision},body.get('request_id'),n))
@router.post('/api/experiments/compare')
def compare_experiments(body:dict=Body(...)):
    from research.validation.comparability import assess_comparability
    run_ids=body.get('run_ids')
    if not isinstance(run_ids,list) or not run_ids or any(not isinstance(rid,str) or not rid.strip() for rid in run_ids) or len(set(run_ids))!=len(run_ids):
        error('INVALID_COMPARISON','Select a nonempty list of unique run IDs',422)
    with Session() as s:
        runs=[get(s,TaskRun,rid) for rid in run_ids]
        if len({r.project_id for r in runs})>1: error('CROSS_PROJECT','Runs belong to different projects')
        rows=[{'id':r.id,'status':r.status,'metrics':r.metrics,'resource':r.resource,'config':{k:v for k,v in r.config.items() if k!='provider_snapshot'}} for r in runs]
        try:comparison=assess_comparability(rows,body.get('objective'))
        except (TypeError,ValueError) as exc:error('INVALID_COMPARISON',str(exc),422)
        return {'runs':rows,**comparison}
@router.post('/api/analysis/run')
def analysis(body:dict=Body(...)):
    with Session.begin() as s:
        run_ids=body.get('run_ids')
        if run_ids is not None:
            if (not isinstance(run_ids,list) or not run_ids
                    or any(not isinstance(run_id,str) or not run_id.strip() for run_id in run_ids)
                    or len(run_ids)!=len(set(run_ids))):
                error('INVALID_RUN_SELECTION','Select a nonempty list of unique run IDs',422)
            runs=[get(s,TaskRun,run_id) for run_id in run_ids]
            if any(run.project_id!=body['project_id'] for run in runs):
                error('CROSS_PROJECT','Selected runs must belong to this project',422)
            if any(run.status!='completed' for run in runs):
                error('INELIGIBLE_RUNS','Only completed runs can be analyzed',422,
                      'Remove queued, running, failed, or cancelled runs from the selection.')
        return asdict(enqueue(s,body['project_id'],'analysis',body,body.get('request_id')))
@router.get('/api/data/{ident}/rows')
def data_rows(ident:str,path:str|None=None,offset:int=0,limit:int=100,filter:str='',sort:str='',descending:bool=False):
    import pandas as pd
    with Session() as s:
        run=s.get(TaskRun,ident); rec=s.get(Analysis,ident) if not run else None
        if not run and not rec: error('NOT_FOUND','Dataset or analysis missing',404)
        pid=run.project_id if run else rec.project_id
        rel=path or (run.output_path+'/predictions.csv' if run else rec.data.get('path',''))
    p=safe_path(project_dir(pid),rel,True)
    frame=pd.read_parquet(p) if p.suffix=='.parquet' else infer_csv_column_types(pd.read_csv(p,dtype=str))
    if filter:
        mask=frame.astype(str).apply(lambda col:col.str.contains(filter,case=False,regex=False)).any(axis=1); frame=frame[mask]
    if sort in frame.columns: frame=frame.sort_values(sort,ascending=not descending)
    with Session() as s: revision=s.scalar(select(FileRevision).where(FileRevision.project_id==pid,FileRevision.path==rel))
    fallback='measured' if run else rec.data.get('source_origin','derived')
    origin=effective_file_origin(revision.origin if revision else None,fallback)
    import math
    page=frame.iloc[max(0,offset):max(0,offset)+min(limit,1000)]
    rows=json.loads(page.to_json(orient='records'))
    # Keep pandas' null and date encoding without rounding finite floats.
    for row,values in zip(rows,page.to_dict(orient='records')):
        for column,value in values.items():
            if isinstance(value,float) and math.isfinite(value): row[column]=value
    return {'columns':list(frame.columns),'rows':rows,'total':len(frame),'origin':origin}
@router.post('/api/figures/{ident}/render')
def render_figure(ident:str,body:dict=Body(default={})):
    with Session.begin() as s:
        f=get(s,Figure,ident); return asdict(enqueue(s,f.project_id,'figure',{'figure_id':ident,'figure_revision':f.revision,'figure':f.data,**body},body.get('request_id')))
@router.post('/api/figures/{ident}/revise')
def revise_figure(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        f=get(s,Figure,ident)
        if body.get('expected_revision',f.revision)!=f.revision: error('REVISION_CONFLICT','Figure changed; select the region again',409)
        return asdict(enqueue(s,f.project_id,'figure_revise',{'figure_id':ident,'figure_revision':f.revision,'figure':f.data,**body},body.get('request_id')))
@router.get('/api/figures/{ident}/export')
def export_figure(ident:str,format:str='svg'):
    with Session() as s: f=get(s,Figure,ident); p=f.data.get('outputs',{}).get(format); pid=f.project_id
    if not p: error('ARTIFACT_UNAVAILABLE','Render this figure before exporting',409)
    return FileResponse(safe_path(project_dir(pid),p,True),filename='figure.'+format)

def ensure_paper(s,project_id):
    p=locked_project_paper(s,project_id)
    if not p:
        source='\\documentclass{article}\n\\usepackage{graphicx}\n\\title{Research manuscript}\n\\author{FOREST workspace}\n\\begin{document}\n\\maketitle\n\\section{Research question}\nWrite the evidence-supported question here.\n\\end{document}\n'
        p=PaperDocument(project_id=project_id,title='Research manuscript',data={'source':source,'bibtex':'','bindings':[],'manually_edited':False,'content_origin':'draft'},status='draft'); s.add(p); s.flush()
    return p
@router.get('/api/papers/{ident}')
def paper(ident:str):
    with Session.begin() as s: p=s.get(PaperDocument,ident) or ensure_paper(s,ident); return asdict(p)
@router.post('/api/papers/{ident}/generate')
def generate_manuscript(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        p=s.get(PaperDocument,ident) or ensure_paper(s,ident)
        if not isinstance(body.get('run_ids'),list) or not body['run_ids']:
            error('EVIDENCE_REQUIRED','Select the completed scientific runs for the full manuscript',422)
        for run_id in body['run_ids']:
            run=get(s,TaskRun,run_id)
            if run.project_id!=p.project_id or run.status!='completed' or run.kind not in ('experiment','command','agent'):
                error('INVALID_EVIDENCE','Manuscript evidence must be completed experiment, command, or agent runs in this project',422)
            from services.interventions.applicability import goal_applicability
            applicable=goal_applicability(s,run)
            if not applicable['ready']:
                error('GOAL_APPLICABILITY_REQUIRED','Review manuscript evidence against the current goal: '+run.id,409)
            from research.paper.evidence import manuscript_metrics_eligibility
            try:
                manuscript_metrics_eligibility(safe_path(project_dir(p.project_id),run.output_path,True),
                    run.config.get('metrics_file','metrics.json'),run_id=run.id)
            except (OSError,ValueError) as exc:
                error('INVALID_EVIDENCE',str(exc),422,suggestion='Select a completed run with a readable metrics file containing finite numeric measurements.')
        return asdict(enqueue(s,p.project_id,'paper_generate',{**body,'manuscript_type':body.get('manuscript_type','full_paper')},body.get('request_id')))
@router.post('/api/papers/{ident}/figures')
def insert_paper_figure(ident:str,body:dict=Body(...)):
    from research.paper.insertion import insert_figure
    with Session.begin() as s:
        p=s.get(PaperDocument,ident) or ensure_paper(s,ident)
        p=locked_paper(s,p.id)
        if body.get('expected_revision')!=p.revision:
            error('REVISION_CONFLICT','Save the current manuscript before inserting a figure',409)
        f=get(s,Figure,body.get('figure_id'))
        if f.project_id!=p.project_id: error('CROSS_PROJECT','Choose a figure from this project',422)
        if f.status not in ('ready_for_review','available'):
            error('FIGURE_UNAVAILABLE','Render and review this figure before inserting it',409)
        outputs=f.data.get('outputs',{})
        chosen=next((outputs.get(key) for key in ('pdf','png','jpg','jpeg') if outputs.get(key)),None)
        if not chosen: error('FIGURE_UNAVAILABLE','A rendered PDF or image is required',409)
        root=project_dir(p.project_id); origin=safe_path(root,chosen,True)
        asset=f'figures/{f.id}/figure{origin.suffix.lower()}'
        try:
            source=insert_figure(p.data.get('source',''),asset_path=asset,caption=body.get('caption') or f.data.get('caption') or f.title,
                label='fig:'+f.id,anchor_text=body.get('anchor_text'),span=body.get('span','column'))
        except ValueError as exc: error('INVALID_FIGURE_ANCHOR',str(exc),422)
        destination=safe_path(root,'paper/'+asset);destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(origin,destination)
        for key in ('source','data','style','report','selection','caption_context'):
            if outputs.get(key):
                original=safe_path(root,outputs[key],True)
                shutil.copy2(original,destination.parent/original.name)
        binding={'figure_id':f.id,'figure_revision':f.revision,'asset_path':asset,'source_run_ids':f.data.get('source_run_ids',[]),
                 'anchor_text':body['anchor_text'],'label':'fig:'+f.id}
        p.data={**p.data,'source':source,'figure_bindings':[*p.data.get('figure_bindings',[]),binding],
            'manually_edited':True,'content_origin':'manual','layout_preflight':None,'layout_plan':None}
        p.revision+=1;p.status='needs_update';write_working_source(p)
        emit(s,p.project_id,'paper_changed',{'id':p.id,'figure_id':f.id})
        return asdict(p)
@router.patch('/api/papers/{ident}')
def edit_paper(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        p=s.get(PaperDocument,ident) or ensure_paper(s,ident)
        p=locked_paper(s,p.id)
        if body.get('expected_revision',p.revision)!=p.revision: error('REVISION_CONFLICT','Manuscript changed in another editor',409)
        p.data={**p.data,**body.get('data',{}),'manually_edited':True,'content_origin':'manual'}; p.title=body.get('title',p.title); p.revision+=1; p.status='needs_update'
        if any(key in body.get('data',{}) for key in ('source','bibtex')):
            p.data={**p.data,'layout_preflight':None,'layout_plan':None}
        write_working_source(p)
        emit(s,p.project_id,'paper_changed',{'id':p.id}); return asdict(p)
@router.post('/api/papers/{ident}/compile')
def compile_paper(ident:str,body:dict=Body(default={})):
    with Session.begin() as s:
        p=s.get(PaperDocument,ident) or ensure_paper(s,ident)
        p=locked_paper(s,p.id)
        if body.get('expected_revision',p.revision)!=p.revision: error('REVISION_CONFLICT','Manuscript changed before compilation was requested',409)
        return asdict(enqueue(s,p.project_id,'paper_compile',{'paper_id':p.id,'paper_revision':p.revision,'source':p.data['source'],'bibtex':p.data.get('bibtex','')},body.get('request_id')))
@router.post('/api/papers/{ident}/layout')
def layout_paper(ident:str,body:dict=Body(...)):
    from research.paper.layout import normalize_layout
    from .paper_state import enqueue_layout
    with Session.begin() as s:
        p=s.get(PaperDocument,ident) or ensure_paper(s,ident)
        p=locked_paper(s,p.id)
        if body.get('expected_revision')!=p.revision: error('REVISION_CONFLICT','Manuscript changed before layout was requested',409)
        template=body.get('template') or p.data.get('template') or ('iclr2027' if 'iclr2027_conference' in p.data.get('source','') else 'article')
        try: layout=normalize_layout({**(p.data.get('layout') or {}),**body.get('layout',{})},template)
        except (ValueError,TypeError) as exc: error('INVALID_LAYOUT',str(exc),422)
        return asdict(enqueue_layout(s,p,layout,template,body.get('request_id')))
@router.post('/api/papers/{ident}/check')
def check_paper(ident:str):
    with Session() as s:
        p=s.get(PaperDocument,ident) or s.scalar(select(PaperDocument).where(PaperDocument.project_id==ident))
        if not p: error('NOT_FOUND','Create a paper first',404)
        source=p.data.get('source',''); refs=p.data.get('bibtex',''); issues=[]
        keys=set(re.findall(r'@\w+\s*\{\s*([^,]+)',refs)); citations=set(k.strip() for c in re.findall(r'\\cite\w*\{([^}]+)\}',source) for k in c.split(','))
        for k in citations-keys: issues.append({'kind':'missing_citation','text':k,'severity':'error'})
        labels=set(re.findall(r'\\label\{([^}]+)\}',source)); used=set(re.findall(r'\\(?:ref|eqref|autoref)\{([^}]+)\}',source))
        for label in used-labels: issues.append({'kind':'undefined_reference','text':label,'severity':'error'})
        from research.paper.writing import review_defensive_writing
        writing=review_defensive_writing(source)
        for finding in writing['edits']:
            issues.append({'kind':'defensive_writing','text':finding['original'],'offset':finding['start'],
                           'severity':'suggestion','fix':finding['reason'],'replacement':finding['replacement'],
                           'requires_evidence_judgment':finding['requires_evidence_judgment']})
        if p.status=='needs_update': issues.append({'kind':'stale_material','severity':'warning','text':p.data.get('stale_reason','Compile latest source and review result bindings.')})
        for b in p.data.get('bindings',[]):
            if isinstance(b,dict) and b.get('path') and not safe_path(project_dir(p.project_id),b['path']).exists(): issues.append({'kind':'missing_material','severity':'error','text':b['path']})
        return {'issues':issues,'writing_profile':writing['profile'],'coverage':'Citation keys, cross references, stale bindings and selected defensive phrases; semantic support remains inspectable in source passages.','status':'needs_review' if issues else 'ready_for_review'}
@router.post('/api/papers/{ident}/revise')
def revise_paper(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        p=s.get(PaperDocument,ident) or ensure_paper(s,ident)
        p=locked_paper(s,p.id)
        if body.get('expected_revision',p.revision)!=p.revision: error('REVISION_CONFLICT','Manuscript changed before revision was requested',409)
        return asdict(enqueue(s,p.project_id,'paper_revise',{**body,'paper_id':p.id,'paper_revision':p.revision,'source':p.data['source']},body.get('request_id')))
@router.post('/api/papers/{ident}/export')
def export_paper(ident:str,body:dict=Body(default={})):
    with Session() as s:
        p=s.get(PaperDocument,ident) or s.scalar(select(PaperDocument).where(PaperDocument.project_id==ident))
        if not p: error('NOT_FOUND','Paper missing',404)
        if body.get('expected_revision',p.revision)!=p.revision:
            error('REVISION_CONFLICT','Manuscript changed before export; read its current revision',409)
        root=project_dir(p.project_id); pdf=p.data.get('pdf_path')
        if body.get('format')=='pdf':
            if not pdf: error('PDF_UNAVAILABLE','Compile the draft first',409)
            if p.data.get('compiled_revision')!=p.revision:
                error('STALE_PDF','Compile the current manuscript revision before exporting its PDF',409)
            return FileResponse(safe_path(root,pdf,True),filename='forest-paper.pdf')
        out=io.BytesIO(); assets={}
        source=body.get('source',p.data['source'])
        bibtex=body.get('bibtex',p.data.get('bibtex',''))
        is_saved_source=source==p.data['source'] and bibtex==p.data.get('bibtex','')
        # Generated LaTeX includes results_macros.tex and figures/... relative
        # to paper.tex, so its source bundle belongs at the ZIP root.
        folders=[]
        if p.data.get('source_dir'): folders.append(safe_path(root,p.data['source_dir'],True))
        working=safe_path(root,'paper')
        if working.exists(): folders.append(working)
        for folder in folders:
            for f in folder.rglob('*'):
                if f.is_file() and not f.is_symlink() and f.suffix not in ('.aux','.blg','.log'):
                    relative=str(f.relative_to(folder))
                    if relative not in ('paper.tex','references.bib','paper.pdf'):
                        assets[relative]=safe_path(root,str(f.relative_to(root)),True)
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('paper.tex',source); z.writestr('references.bib',bibtex)
            for relative,path in sorted(assets.items()): z.write(path,relative)
            if is_saved_source and pdf and p.data.get('compiled_revision')==p.revision and safe_path(root,pdf).exists(): z.write(safe_path(root,pdf),'paper.pdf')
        return Response(out.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="forest-paper-source.zip"'})

# Separate SQL tables for research object types, with uniform revision-aware editing.
def install_resource_routes(name,model):
    def lock_record(s,ident):
        from services.worker.scheduler import _lock_project
        project_id=s.scalar(select(model.project_id).where(model.id==ident))
        if not project_id:error('NOT_FOUND','Research object missing',404)
        _lock_project(s,project_id)
        return get(s,model,ident,for_update=True)
    def listing(project_id:str,limit:int=100,offset:int=0):
        with Session() as s: get(s,Project,project_id); return [asdict(r) for r in s.scalars(select(model).where(model.project_id==project_id).order_by(model.updated_at.desc()).limit(min(limit,500)).offset(max(0,offset)))]
    def create(body:ResourceCreate):
        with Session.begin() as s:
            from services.worker.scheduler import _lock_project
            _lock_project(s,body.project_id)
            from services.interventions.dependencies import validate_bindings
            validate_bindings(s,body.project_id,body.data)
            r=model(**body.model_dump()); s.add(r); s.flush(); emit(s,r.project_id,'artifact_available',{'kind':name,'id':r.id}); return asdict(r)
    def read(ident:str):
        with Session() as s: return asdict(get(s,model,ident))
    def edit(ident:str,body:dict=Body(...)):
        with Session.begin() as s:
            # Project -> resource -> dependent records -> event cursor.
            r=lock_record(s,ident)
            if body.get('expected_revision',r.revision)!=r.revision: error('REVISION_CONFLICT','This research object changed',409)
            invalidated_figure=False
            if 'title' in body: r.title=body['title']
            if 'data' in body:
                from services.interventions.dependencies import validate_bindings
                if body.get('replace_data') is True:
                    validate_bindings(s,r.project_id,body['data'])
                    updated=body['data']
                else:
                    validate_bindings(s,r.project_id,{**r.data,**body['data']})
                    updated={**r.data,**body['data']}
                render_inputs=('kind','style','code','code_origin','run_ids','metric','data','caption','purpose','image_prompt','narrative_mode','image_variants','candidates')
                if name=='figures' and any(updated.get(key)!=r.data.get(key) for key in render_inputs):
                    for key in ('outputs','svg_path','png_path','pdf_path','jpg_path','jpeg_path','visual_selection'):
                        updated.pop(key,None)
                    updated['visual_review_status']='stale'
                    invalidated_figure=True
                r.data=updated
            if 'status' in body: r.status=body['status']
            if invalidated_figure: r.status='needs_review'
            r.revision+=1; touch_dependents(s,r.project_id,r.id); return asdict(r)
    def remove(ident:str):
        with Session.begin() as s:
            r=lock_record(s,ident)
            touch_dependents(s,r.project_id,ident)
            if model is SourcePaper:
                passages=s.scalars(select(SourcePassage).where(
                    SourcePassage.project_id==r.project_id
                ))
                for passage in passages:
                    if passage.data.get('paper_id')==ident: s.delete(passage)
            if model is Hypothesis:
                snapshot={'id':r.id,'title':r.title,'revision':r.revision,'status':r.status,'data':r.data}
                graph_changed=False
                for node in s.scalars(select(Node).where(Node.project_id==r.project_id)):
                    updated=[]
                    changed=False
                    for ref in node.inputs or []:
                        if (isinstance(ref,dict) and ref.get('kind')=='idea' and ref.get('id')==ident
                                and ref.get('snapshot')!=snapshot):
                            ref={**ref,'snapshot':snapshot}
                            changed=True
                        updated.append(ref)
                    if changed:
                        node.inputs=updated
                        node.revision+=1
                        graph_changed=True
                if graph_changed:
                    project=s.get(Project,r.project_id)
                    project.revision+=1
                    emit(s,project.id,'project_changed',{'revision':project.revision})
            s.delete(r)
            return {'deleted':ident}
    router.add_api_route('/api/'+name,listing,methods=['GET'],name=name+'_list')
    if name!='reviews': router.add_api_route('/api/'+name,create,methods=['POST'],name=name+'_create')
    router.add_api_route('/api/'+name+'/{ident}',read,methods=['GET'],name=name+'_get')
    router.add_api_route('/api/'+name+'/{ident}',edit,methods=['PATCH'],name=name+'_edit')
    router.add_api_route('/api/'+name+'/{ident}',remove,methods=['DELETE'],name=name+'_delete')
for name,model in RESOURCE_MODELS.items(): install_resource_routes(name,model)

@router.post('/api/projects/{ident}/research/{action}')
def research_control(ident:str,action:str,body:dict=Body(default={})):
    intervention_id=None
    with Session.begin() as s:
        from services.worker.scheduler import _lock_project
        p=_lock_project(s,ident)
        control=p.config.get('controller',{}); affected=[]
        from services.interventions.models import Intervention
        old=s.scalar(select(Intervention).where(Intervention.project_id==ident,
            Intervention.request_id==body.get('request_id'))) if body.get('request_id') else None
        if old:
            from services.interventions.application import readback
            requested={'start':'resume','pause':'pause','stop':'cancel'}.get(action)
            if old.actor!='owner_controller' or old.intent.get('action')!=requested:
                error('REQUEST_ID_CONFLICT','Request identity belongs to different controller intent',409)
            parameters={key:value for key,value in body.items() if key not in ('request_id','expected_revision')}
            if old.intent.get('parameters',{})!=parameters:
                error('REQUEST_ID_CONFLICT','Request identity belongs to different controller parameters',409)
            return {**control,'intervention':readback(s,old),'process_control_errors':[]}
        if action=='start':
            parallelism=body.get('ready_parallelism',control.get('ready_parallelism',1))
            if type(parallelism) is not int or not 1<=parallelism<=32:
                error('INVALID_PARALLELISM','ready_parallelism must be an integer from 1 to 32',422)
            branch_id=body.get('branch_id',control.get('branch_id'))
            if branch_id:
                selected_branch=s.get(Branch,branch_id)
                if selected_branch is None or selected_branch.project_id!=ident:
                    error('UNKNOWN_BRANCH','Choose a research branch from this project',404)
                if selected_branch.status!='active':
                    error('BRANCH_NOT_RUNNABLE','Restore this branch before starting research on it',409)
            affected=control.get('paused_run_ids',[])
            control={**control,'status':'running','phase':'PLAN','branch_id':branch_id,'required_artifacts':body.get('required_artifacts',control.get('required_artifacts',[])), 'autonomous':body.get('autonomous',control.get('autonomous',p.mode=='auto')),'ready_parallelism':parallelism}
            # Old clients/checkpoints may carry a planning-cycle cap. Preserve
            # it as history only; research lifetime follows goals and resources.
            legacy_limit=control.pop('max_cycles',body.get('max_cycles'))
            if legacy_limit is not None: control['legacy_max_cycles']=legacy_limit
            control.pop('reason',None);control.pop('paused_run_ids',None)
        elif action in ('pause','stop'):
            from services.worker.scheduler import ACTIVE
            q=select(TaskRun).where(TaskRun.project_id==ident,TaskRun.status.in_(ACTIVE))
            if control.get('branch_id'):q=q.where(TaskRun.branch_id==control['branch_id'])
            affected=[r.id for r in s.scalars(q) if action=='stop' or r.status in ('running','waiting','queued')]
            control={**control,'status':'paused' if action=='pause' else 'stopped'}
            if action=='pause':control['paused_run_ids']=affected
            if action=='stop':
                from services.interventions.application import accept_cancel_in_session
                receipt=accept_cancel_in_session(s,p,body.get('request_id') or uid(),affected,
                    expected_revision=body.get('expected_revision'),actor='owner_controller',
                    parameters={key:value for key,value in body.items() if key not in ('request_id','expected_revision')})
                intervention_id=receipt.id
        else: error('UNKNOWN_ACTION','Use start, pause or stop',404)
        if action in ('pause','start'):
            from services.interventions.lifecycle import accept_lifecycle
            receipt=accept_lifecycle(s,p,body.get('request_id') or uid(),affected,
                'pause' if action=='pause' else 'resume',body=body,actor='owner_controller')
            intervention_id=receipt.id
        p.config={**p.config,'controller':control};emit(s,ident,'controller_changed',control)
    if intervention_id:
        from services.interventions.application import kick_effects,readback
        from services.interventions.models import Intervention
        kick_effects(intervention_id=intervention_id,limit=10)
        with Session() as s:receipt=readback(s,get(s,Intervention,intervention_id))
        return {**control,'intervention':receipt,'process_control_errors':[]}
    return {**control,'process_control_errors':[]}

@router.post('/api/research/proposals/{ident}/apply')
def apply_proposal(ident:str,body:dict=Body(...)):
    from services.interventions.application import apply_in_session,kick_effects,readback
    from services.interventions.models import Intervention
    from services.worker.scheduler import _lock_project
    with Session() as reader: project_id=get(reader,Hypothesis,ident).project_id
    with Session.begin() as s:
        p=_lock_project(s,project_id);proposal=get(s,Hypothesis,ident)
        if proposal.status=='rejected':error('PROPOSAL_REJECTED','This proposal was rejected; submit a revised proposal',409)
        commands=body.get('commands',proposal.data.get('commands',[]))
        selected=body.get('indices',list(range(len(commands))))
        if not selected or any(type(i) is not int or not 0<=i<len(commands) for i in selected):
            error('INVALID_SELECTION','Choose valid proposed commands',422)
        request_id=body.get('request_id') or uid()
        accepted=set(proposal.data.get('accepted_indices',[]))
        receipt=s.scalar(select(CommandReceipt).where(CommandReceipt.project_id==p.id,
            CommandReceipt.request_id==request_id))
        if not receipt and accepted.intersection(selected):
            error('PROPOSAL_ALREADY_APPLIED','An accepted suggestion cannot be applied again',409)
        result=apply_in_session(s,p,request_id,body.get('expected_revision',proposal.data.get('graph_revision')),
                                [commands[i] for i in selected],actor='owner_proposal',batch=True,
                                origin={'proposal_id':ident,'indices':selected})
        if not receipt:
            applied_commands={**proposal.data.get('applied_commands',{}),**{str(i):commands[i] for i in selected}}
            proposal.data={**proposal.data,'accepted_indices':sorted(accepted.union(selected)),
                           'applied_commands':applied_commands}
            proposal.status='partly_adopted' if len(proposal.data['accepted_indices'])<len(commands) else 'adopted'
        result['accepted_indices']=selected;intervention_id=result['intervention']['id']
    kick_effects(intervention_id=intervention_id,limit=10)
    with Session() as s:result['intervention']=readback(s,get(s,Intervention,intervention_id))
    return result

@router.post('/api/reviews/{ident}/apply')
def apply_revision(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        review=get(s,Review,ident); p=locked_paper(s,review.data.get('paper_id'))
        if body.get('expected_revision')!=p.revision or review.data.get('paper_revision')!=p.revision: error('REVISION_CONFLICT','Paper changed; generate a fresh proposal or manually merge the exact edits',409)
        source=p.data['source']; indices=body.get('indices',[])
        if review.data.get('kind')=='paper_generation':
            apply_generation_review(s,review,p,indices); return asdict(p)
        if not indices or any(type(i) is not int or i<0 or i>=len(review.data.get('edits',[])) for i in indices): error('INVALID_SELECTION','Select valid proposed edits',422)
        for i in indices:
            item=review.data['edits'][i]
            if source.count(item['original'])!=1: error('AMBIGUOUS_SPAN','The original passage must occur exactly once',409)
            source=source.replace(item['original'],item['replacement'],1)
        p.data={**p.data,'source':source,'manually_edited':True,'content_origin':'review_applied'}; p.revision+=1; p.status='needs_update'; review.status='applied'; write_working_source(p); emit(s,p.project_id,'paper_changed',{'id':p.id}); return asdict(p)

@router.post('/api/projects/{ident}/runs/selected')
def run_selected(ident:str,body:dict=Body(...)):
    from services.worker.scheduler import enqueue_selected
    with Session.begin() as s:
        get(s,Project,ident)
        for nid in body.get('node_ids',[]):
            if get(s,Node,nid).project_id!=ident: error('CROSS_PROJECT','Selected node is outside this project')
        runs=enqueue_selected(s,body.get('node_ids',[]),body.get('request_id'),body.get('config'))
        return {'runs':[asdict(r) for r in runs],'run_ids':[r.id for r in runs]}

@router.post('/api/browser/read')
def browser_read(body:dict=Body(...)):
    from research.literature.browser import read_page
    with Session() as s: get(s,Project,body['project_id'])
    root=project_dir(body['project_id']); folder=root/'library'/('page-'+uid())
    result=read_page(body['url'],folder,body.get('screenshot',False))
    for key in ('text_path','screenshot_path'):
        if result.get(key): result[key]=str(Path(result[key]).relative_to(root))
    with Session.begin() as s:
        r=SourcePaper(project_id=body['project_id'],title=result['title'] or body['url'],data={**result,'source':'public_web_page'},status='available'); s.add(r); s.flush(); emit(s,r.project_id,'artifact_available',{'kind':'library','id':r.id}); saved=asdict(r)
        saved['data']={**saved['data'],'passages':saved['data'].get('passages',[])[:1],
                       'passage_count':len(saved['data'].get('passages',[]))}
        return saved

@router.patch('/api/runs/{ident}/checkpoint')
def edit_checkpoint(ident:str,body:dict=Body(...)):
    with Session.begin() as s:
        r=get(s,TaskRun,ident)
        if r.status!='waiting_input' or not r.resource.get('checkpoint'): error('NO_CHECKPOINT','This run is not paused at an editable debug checkpoint',409)
        point=r.resource['checkpoint']; r.resource={**r.resource,'checkpoint':{**point,'override':body.get('payload')}}; return {'checkpoint':r.resource['checkpoint'],'status':r.status}
