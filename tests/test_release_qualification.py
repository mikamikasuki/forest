"""Qualification tests use actual files, arithmetic and HTTP/worker processes.

They do not substitute a model or fabricate successful model responses.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
from sklearn.datasets import load_digits

from scripts.package_source import package
from scripts.qualification.tasks import corpus, check, compare
from scripts.qualify_release import API, graph, compute, soak, prime_oracle, task_instruction, AGENT_CONTEXT_CHARS
from test_worker import Harness


def test_paid_qualification_rejects_actual_unpriced_provider_before_inference(tmp_path):
    from scripts.qualify_release import agents
    h = Harness(tmp_path); api = None
    try:
        h.start_api(); api = API(h.base_url)
        provider = api('POST', '/api/providers', json={'name': 'Unpriced CLI accounting guard',
            'kind': 'codex_cli', 'base_url': 'http://127.0.0.1', 'model': 'gpt-6-luna',
            'allow_paid': False, 'config': {'budget_usd': 1}})
        usage = api('GET', '/api/providers/' + provider['id'] + '/usage')
        assert usage['remaining_usd'] is None and usage['request_count'] == 0
        args = argparse.Namespace(provider_id=provider['id'], task=['01-prime-independence'],
            allow_paid=True, max_cost_usd=1)
        with pytest.raises(ValueError, match='remaining USD is unknown'):
            agents(api, args, {})
        assert api('GET', '/api/providers/' + provider['id'] + '/usage')['request_count'] == 0
        assert api('GET', '/api/projects') == []
    finally:
        if api: api.client.close()
        h.cleanup()


def test_corpus_has_thirty_distinct_bounded_tasks_and_real_evidence():
    tasks=corpus()
    assert len(tasks)==len({t.id for t in tasks})==30
    assert {t.category for t in tasks}=={'math','code','data','files','writing'}
    assert tasks[0].id=='01-prime-independence'
    fixtures=Path(__file__).resolve().parents[1]/'scripts/qualification/fixtures'
    _,labels=load_digits(return_X_y=True)
    provenance=json.loads((fixtures/'provenance.json').read_text())
    for name,source in provenance['runs'].items():
        rows=list(csv.DictReader(io.StringIO((fixtures/(name+'.csv')).read_text())))
        assert len(rows)==450 and len({r['observation_id'] for r in rows})==450
        for row in rows:
            assert int(float(row['label']))==labels[int(float(row['observation_id']))]
        accuracy=sum(max(range(10),key=lambda i:float(r['p'+str(i)]))==int(float(r['label'])) for r in rows)/len(rows)
        assert accuracy==pytest.approx(source['metrics']['accuracy'],abs=1e-12)


def test_exact_math_expectations_are_recomputed():
    tasks={t.id:t for t in corpus()}
    task=tasks['02-rational-system']; inputs=json.loads(task.inputs['input.json'])
    actual=np.linalg.solve(inputs['A'],inputs['b'])
    assert actual==pytest.approx([float(__import__('fractions').Fraction(x)) for x in task.expected['x']])
    task=tasks['06-exact-probability']; outcomes=[(i,j) for i in range(1,7) for j in range(1,7) if i+j>=8]
    assert len(outcomes)==task.expected['eligible']
    assert sum(i==6 or j==6 for i,j in outcomes)==task.expected['favorable']


def test_oracle_rejects_missing_wrong_nonfinite_and_unbound_outputs():
    for task in corpus(): assert check(task,{})
    assert compare({'value':1.0},{'value':float('nan')})
    assert compare({'value':1},{'value':True})
    assert compare({'rows':[1,2]},{'rows':[1]})
    task=next(t for t in corpus() if t.category=='writing')
    artifacts={'solution.py':'# placeholder is not execution evidence','result.json':json.dumps(task.expected),'report.md':'An unsupported performance claim.'}
    failures=check(task,artifacts)
    assert any('source binding' in f for f in failures)
    assert any('word count' in f for f in failures)


@pytest.mark.parametrize(('means', 'unbound'), [
    ('The baseline mean is 0.913333 and the candidate mean is 0.906667.', ()),
    ('**Baseline mean:** 0.913 and **candidate mean:** 0.907.', ()),
    ('The baseline mean is 0.906667 and the candidate mean is 0.913333.', ('baseline_mean', 'candidate_mean')),
    ('The two means are 0.913333 and 0.906667.', ('baseline_mean', 'candidate_mean')),
    ('Baseline mean: 0.913333. The baseline mean was 0.906667. Candidate mean: 0.906667.', ('baseline_mean',)),
    ('Baseline mean: 0.913333 %. Candidate mean: 0.906667.', ('baseline_mean',)),
])
def test_writing_oracle_binds_reported_means_to_labels(means, unbound):
    task = next(task for task in corpus() if task.id == '25-paired-accuracy')
    report = (
        f'{means} The signed improvement is -0.006667. Baseline is the better method across '
        '450 paired observations. The observations are paired by observation_id. The seed and '
        'regularization settings differ, and the compute budget was unequal, so the comparison '
        'is confounded and provides no causal or generalizable evidence. A confidence increase '
        'is not necessarily a quality improvement. This analysis summarizes the supplied split '
        'and the observed prediction results. The measured predictions are saved outputs '
        '[data:input.json] and use the dataset provenance [source:qualification-digits].'
    )
    artifacts = {
        'solution.py': '# Report validation fixture',
        'result.json': json.dumps(task.expected),
        'report.md': report,
    }
    failures = check(task, artifacts)
    assert len(failures) == len(unbound)
    for field in unbound:
        assert any(field in failure for failure in failures)
    if unbound:
        assert check(task, artifacts, oracle_revision=2) == []


def test_rescore_leaves_ungraded_attempt_for_resume(tmp_path):
    from scripts.qualify_release import rescore

    record = {'id': '25-paired-accuracy', 'status': 'running', 'run_id': 'run-1',
              'conditions': {'corpus_version': 4, 'oracle_revision': 2}}
    report = {'tasks': [record.copy()]}

    def read_run(*_args):
        pytest.fail('An ungraded attempt must be resumed before any rescore reads')

    rescore(read_run, argparse.Namespace(task=None, report=tmp_path / 'release-agents.json'), report)
    assert report['tasks'] == [record]
    assert report['rescore_operations'][-1]['records'] == []


def test_prime_oracle_rejects_algorithm_alias_and_fabricated_counts():
    artifacts={'metrics.json':json.dumps({'trial_count':1229,'sieve_count':1229}),'report.md':'Two names are not two independent algorithms.','alias.py':'def trial(n):\n return [i for i in range(2,n+1) if all(i%d for d in range(2,i))]\ndef sieve(n):\n return trial(n)\n'}
    # No actual second mechanism: a wrapper named sieve is not sufficient.
    failures,_=prime_oracle(artifacts)
    assert failures
    artifacts['metrics.json']='{"trial_count":1230,"sieve_count":1230}'
    assert any('counts' in f for f in prime_oracle(artifacts)[0])


def test_source_archive_excludes_runtime_and_credentials(tmp_path):
    (tmp_path/'README.md').write_text('Public source')
    (tmp_path/'services').mkdir(); (tmp_path/'services/api.py').write_text('print(1)')
    (tmp_path/'services/.env.local').write_text('TOKEN=private')
    (tmp_path/'services/credentials.json').write_text('{"token":"private"}')
    (tmp_path/'var').mkdir(); (tmp_path/'var/state.json').write_text('private')
    (tmp_path/'output').mkdir(); (tmp_path/'output/result.json').write_text('private')
    target=tmp_path/'bundle.zip'; package(target,tmp_path)
    with zipfile.ZipFile(target) as z:
        assert set(z.namelist())=={'forest/README.md','forest/services/api.py'}
    (tmp_path/'services/leak.py').write_text('key="sk-'+('x'*30)+'"')
    with pytest.raises(ValueError,match='Possible secret'): package(target,tmp_path)


@pytest.mark.parametrize('nodes', [200, 201, 1000])
def test_documented_graph_qualification_scales_through_actual_api(tmp_path, nodes):
    """Exercise the documented command above the public per-batch limit."""
    harness = Harness(tmp_path); api = None
    try:
        harness.start_api(); api = API(harness.base_url)
        report = {}
        graph(api, argparse.Namespace(nodes=nodes, report=tmp_path/'graph.json'), report)
        assert report['status'] == 'passed' and report['node_count'] == nodes
        assert report['edge_count'] == nodes - 1
        assert report['executed_compute_nodes'] == 0
        assert api('GET', f"/api/projects/{report['project_id']}/runs") == []
    finally:
        if api: api.client.close()
        harness.cleanup()


def test_real_graph_api_and_real_compute_qualification(tmp_path):
    harness=Harness(tmp_path); api=None
    try:
        harness.start_api(); harness.start_worker(); api=API(harness.base_url)
        args=argparse.Namespace(nodes=40,report=tmp_path/'graph.json')
        report={}; graph(api,args,report)
        assert report['status']=='passed' and report['node_count']==40
        assert report['executed_compute_nodes']==0 and report['observed_seconds']>0
        args=argparse.Namespace(seconds=.2,poll=.1,submit_only=False,report=tmp_path/'compute.json')
        report={'mode':'compute','url':harness.base_url}; compute(api,args,report)
        assert report['status']=='passed',report
        assert report['observed_compute_seconds']>=.2
        assert report['metrics']['n_test']==450
        args=argparse.Namespace(seconds=2,poll=.1,interval=.05,report=tmp_path/'soak.json')
        report={'mode':'soak','url':harness.base_url}; soak(api,args,report)
        assert report['status']=='passed' and report['observed_seconds']>=2
        assert report['checks'] and all(c['run_id'] for c in report['checks'])
        from scripts.qualification_service import supervise
        before=api('GET','/api/projects/'+report['project_id']+'/runs')
        service_args=argparse.Namespace(compute_report=tmp_path/'compute.json',soak_report=tmp_path/'soak.json',url=harness.base_url,soak_seconds=2,log_dir=tmp_path/'monitors',retry_delay=60,supervisor_poll=.1)
        assert supervise(service_args)==0
        assert json.loads((tmp_path/'monitors/status.json').read_text())['status']=='finished'
        assert api('GET','/api/projects/'+report['project_id']+'/runs')==before
    finally:
        if api: api.client.close()
        harness.cleanup()


def test_all_task_controls_fit_actual_runtime_context_builder(tmp_path):
    from research.agents.runtime import context_packet_char_budget
    from research.kernel import ContextBuilder
    capacity=context_packet_char_budget({'context_char_budget':AGENT_CONTEXT_CHARS})
    for task in corpus():
        instructions=task_instruction(task)
        g={'nodes':[{'id':'task','branch_id':'branch','title':task.id,'instructions':instructions,'config':{'kind':'agent'}}], 'branches':[{'id':'branch','workspace':'workspace'}], 'edges':[], 'goal':'Complete bounded executable qualification with actual artifacts.', 'budget':{'allow_paid':False}}
        packet=ContextBuilder(g,tmp_path).build('task','Engineer',{'max_chars':capacity})
        assert packet['controls']['instructions']==instructions
        # Static packet allocation is a soft preview hint. Policy growth must
        # not cause task controls to be dropped to satisfy an obsolete hint.
        preview=packet['capacity']
        assert preview['max_chars']==capacity
        assert preview['used_content_chars']==len(packet['text'])
        assert preview['used_content_chars']<=preview['effective_preview_chars']
        assert preview['effective_preview_chars']>=preview['required_control_chars']
        from research.agents.runtime import model_task_message
        config={'instructions':instructions,'required_outputs':list(task.outputs),
                'metrics_file':'result.json','metrics_required_keys':list(task.expected)}
        message=json.loads(model_task_message(packet,config)['content'])
        assert message['task']==instructions
        assert message['required_outputs']==list(task.outputs)
        assert message['metrics_required_keys']==list(task.expected)


def test_actual_report_lock_prevents_second_monitor(tmp_path):
    import subprocess
    import sys
    from scripts.qualify_release import report_lock
    from scripts.qualification_service import monitor_owner
    path=tmp_path/'report.json'
    with report_lock(path):
        assert monitor_owner(path)['pid']==__import__('os').getpid()
        second=subprocess.run([sys.executable,'scripts/qualify_release.py','compute','--report',str(path),'--resume'],capture_output=True,text=True)
        assert second.returncode!=0 and 'Another monitor owns' in second.stderr
        assert not path.exists()
    assert monitor_owner(path) is None


def test_numeric_oracle_accepts_equal_integer_float_but_not_booleans():
    assert compare({'count':10,'mean':5.0},{'count':10.0,'mean':5})==[]
    assert compare({'count':1},{'count':True})
    assert compare({'flag':True},{'flag':1})
    assert compare({'mean':5.0},{'mean':5.1})
    assert compare({'count':10},{'count':10.000001})
    polynomial=next(t for t in corpus() if t.id=='04-polynomial-calculus')
    assert polynomial.expected['definite_integral']=='145/4'
    assert compare(polynomial.expected,{**polynomial.expected,'definite_integral':'103/4'})


def test_current_contract_types_and_unambiguous_text_counts():
    from scripts.qualification.tasks import VERSION,ORACLE_REVISION
    from scripts.qualify_release import result_types
    assert VERSION==5 and ORACLE_REVISION==3
    for task in corpus():
        contract=result_types(task.expected)
        assert set(contract)==set(task.expected)
        assert set(contract.values()) <= {'number','string','array','object','boolean','null'}
        assert json.dumps(contract,sort_keys=True) in task_instruction(task)
    task=next(t for t in corpus() if t.id=='19-unicode-file-count')
    assert 'integer COUNTS' in task.prompt and 'Do not return arrays' in task.prompt
    assert task.inputs['text.txt']=='Café forest\n研究 trees 🌲\nnaïve science\n'


def test_writing_metadata_contract_is_explicit_without_numeric_answers():
    for task in (t for t in corpus() if t.category=='writing'):
        assert 'data_file is the task-manifest filename "input.json"' in task.prompt
        assert 'source_id is provenance.json source_id ("qualification-digits")' in task.prompt
        assert 'unit is input.json paired_unit ("observation_id")' in task.prompt
        for key in ('baseline_mean', 'candidate_mean', 'improvement', 'n_pairs'):
            assert str(task.expected[key]) not in task.prompt
        assert task.expected['data_file']=='input.json'
        assert task.expected['source_id']==json.loads(task.inputs['provenance.json'])['source_id']
        assert task.expected['unit']==json.loads(task.inputs['input.json'])['paired_unit']


def test_console_summary_omits_receipts_without_mutating_saved_report():
    import copy
    from scripts.qualify_release import brief_report
    report={'mode':'agents','status':'passed','summary':{'passed':30},
            'provider_usage_latest':{'limit_usd':5,'estimated_cost_usd':1.2,'reserved_usd':0,'remaining_usd':3.8,'requests':[{'id':'actual-receipt-reference'}]},
            'tasks':[{'id':'preserved-task'}]}
    original=copy.deepcopy(report)
    summary=brief_report(report,'qualification.json')
    assert summary['shared_provider_cost_usd']['estimated_cost_usd']==1.2
    assert 'requests' not in json.dumps(summary) and 'tasks' not in summary
    assert report==original


def test_prior_monitor_error_is_archived_without_changing_task_evidence():
    import copy
    from scripts.qualify_release import archive_monitor_error, brief_report
    report={'status':'failed','error':'old connection failure','error_recorded_at':'2026-01-01T00:00:00Z',
            'updated_at':'2026-01-02T00:00:00Z','tasks':[{'status':'failed','failures':['actual model error']}],
            'rescored_summary':{'passed':19},'summary':{'passed':29}}
    tasks=copy.deepcopy(report['tasks'])
    assert archive_monitor_error(report)
    assert 'error' not in report and 'error_recorded_at' not in report
    assert report['monitor_error_history'][0]['recorded_at']=='2026-01-01T00:00:00Z'
    assert report['tasks']==tasks and report['status']=='failed'
    assert report['rescored_summary']=={'passed':19}
    assert 'rescored_summary' not in brief_report(report,'report.json')
    assert not archive_monitor_error(report) and len(report['monitor_error_history'])==1
    legacy={'status':'passed','error':'legacy error','updated_at':'2026-01-01T00:00:00Z'}
    assert archive_monitor_error(legacy)
    assert legacy['monitor_error_history'][0]['recorded_at_basis']=='legacy_updated_at'


def test_actual_compute_monitor_resume_after_api_outage_retains_same_run(tmp_path):
    import sys
    from test_worker import wait_until
    harness=Harness(tmp_path); api=None
    try:
        harness.start_api(); api=API(harness.base_url)
        path=tmp_path/'resume-compute.json'
        args=argparse.Namespace(seconds=.2,poll=.05,submit_only=True,report=path)
        report={'mode':'compute','url':harness.base_url}
        compute(api,args,report)
        run_id=report['run_id']
        command=[sys.executable,'scripts/qualify_release.py','compute','--resume','--url',harness.base_url,
                 '--report',str(path),'--seconds','.2','--poll','.05']
        first=harness.spawn('first-compute-monitor',command)
        read=lambda:json.loads(path.read_text())
        wait_until(lambda:read().get('last_run_observation',{}).get('run_id')==run_id)
        harness.stop(harness.api)
        wait_until(lambda:first.poll() is not None)
        interrupted=read()
        assert interrupted['status']=='error' and interrupted['error']
        error_time=interrupted['error_recorded_at']
        harness.start_api()
        second=harness.spawn('resumed-compute-monitor',command)
        wait_until(lambda:read().get('status')=='running' and bool(read().get('monitor_error_history')))
        resumed=read()
        assert resumed['run_id']==run_id and resumed['run_status']=='queued'
        assert resumed['last_run_observation']['run_id']==run_id
        assert resumed['updated_at']>error_time and 'error' not in resumed
        assert resumed['monitor_error_history'][-1]['recorded_at']==error_time
        assert 'observed_compute_seconds' not in resumed
        harness.start_worker()
        wait_until(lambda:second.poll() is not None,30)
        finished=read()
        assert second.returncode==0 and finished['status']=='passed',finished
        assert finished['run_id']==run_id and finished['observed_compute_seconds']>=.2
        runs=api('GET','/api/projects/'+finished['project_id']+'/runs')
        assert len(runs)==1 and runs[0]['id']==run_id
    finally:
        if api:api.client.close()
        harness.cleanup()
