"""Purpose gates with actual producer/checker/consumer processes on both DBs."""
import json
import sys

import pytest
from tests.test_intervention_worker import intervention_harness
from tests.test_research_verification_workflow import add_node, arithmetic_command, verification_config
from tests.test_worker import wait_until


def assert_admission_blocks_worker(h, run):
    blocked = wait_until(lambda: (value if (value := h.run(run))['status'] == 'waiting_input' else None))
    assert blocked['resource']['blocked_reason'] == 'acceptance_contract', blocked
    assert not (h.output(run)/'workspace'/'metrics.json').exists()
    h.control_request(f"/api/runs/{run['id']}/cancel", json={})


@pytest.mark.parametrize(('purpose', 'confirmation'), [
    ('raw_data', None), ('comparison', None), ('major_claim', 'independent_confirmation'),
    ('major_claim', 'adaptive'), ('major_claim', 'legacy'),
])
def test_actual_purpose_handoff_and_changed_evidence(intervention_harness, purpose, confirmation):
    h = intervention_harness
    h.start_api(); h.start_worker()
    project = h.request('POST', '/api/projects', json={'name': 'Purpose-bound numerical handoff',
        'mode': 'manual', 'goal': 'Check arithmetic protocol admission',
        'budget': {'max_runs': 30, 'seconds': 180, 'allow_paid': False}})
    signature = {'dataset': 'integer-sequence', 'dataset_version': '1', 'split': 'fixed-1-to-500',
        'evaluation_protocol': 'integer-square-sum', 'metric': 'score', 'statistical_unit': 'integer',
        'budget': {'evaluations': 500}}
    if confirmation:
        definition = {field: signature[field] for field in ('dataset', 'dataset_version', 'split')}
        signature['evaluation_protocol'] = {'definition': definition}
        protocols = f"/api/projects/{project['id']}/evaluation-protocols"
        selection = h.request('POST', protocols, json={'definition': definition, 'classification': 'exploratory'})
    sources = []
    for index in range(3 if purpose == 'major_claim' else 2 if purpose == 'comparison' else 1):
        config = {'kind': 'experiment', 'command': arithmetic_command(500),
                  'comparison': signature, 'research_phase': 'confirmation' if index == 2 else 'selection'}
        if confirmation and index < 2:
            config['evaluation_protocol_version_id'] = selection['id']
        elif confirmation and confirmation != 'legacy':
            protocol = {'definition': definition, 'classification': 'independent_confirmation'}
            if confirmation == 'adaptive':
                protocol.update(protocol_id=selection['protocol_id'], feedback_run_ids=[sources[0]['id']])
            version = h.request('POST', protocols, json=protocol)
            assert version['classification'] == confirmation
            config['evaluation_protocol_version_id'] = version['id']
        producer = add_node(h, project, type='experiment', title='Actual independent run '+str(index), config=config)
        if purpose == 'raw_data':
            script = """import csv,json
from pathlib import Path
with Path('data.csv').open('w') as handle:
    writer=csv.writer(handle); writer.writerow(['sample','split','value'])
    for i in range(500): writer.writerow(['sample-'+str(i),'train' if i<400 else 'test',i*i])
Path('units.json').write_text(json.dumps({'units':{'value':'dimensionless'}}))
Path('metrics.json').write_text(json.dumps({'score':sum(i*i for i in range(500))}))
"""
            h.request('PATCH', '/api/nodes/'+producer['id'], json={'config': {**config,
                'command': [sys.executable, '-c', script]}})
            verifier_config = {'kind': 'verification', 'command': arithmetic_command(5), 'verification': {
                'producer_node_id': producer['id'], 'checks': [{'id': 'raw-schema', 'kind': 'data_contract',
                    'source': 'data.csv', 'units_file': 'units.json', 'identity_columns': ['sample'],
                    'columns': {'sample': {'type': 'string', 'nullable': False},
                        'split': {'type': 'string', 'nullable': False},
                        'value': {'type': 'integer', 'nullable': False, 'unit': 'dimensionless'}},
                    'split': {'column': 'split', 'identity_columns': ['sample'],
                              'expected_counts': {'train': 400, 'test': 100}}}]}}
        else: verifier_config = verification_config(producer)
        verifier = add_node(h, project, type='verification', title='Executed distinct checker '+str(index), config=verifier_config)
        source = h.terminal(h.launch(producer)); assert source['status'] == 'completed'
        if confirmation and index == 2:
            context = source['config'].get('evaluation_context')
            if confirmation == 'legacy':
                assert context is None
            else:
                assert context['classification'] == confirmation
                assert context['feedback_run_ids'] == ([sources[0]['id']] if confirmation == 'adaptive' else [])
        checked = h.launch(verifier); assert h.terminal(checked)['status'] == 'completed'
        assert h.request('GET', f"/api/runs/{source['id']}/verification")['verification_status'] == 'accepted'
        sources.append(source)
    contract = {'purpose': purpose}
    if purpose == 'raw_data': contract['artifact_paths'] = ['data.csv']
    if purpose == 'major_claim': contract['confirmation_run_ids'] = [sources[2]['id']]
    config = {'kind': 'command', 'command': arithmetic_command(5),
        'run_ids': [source['id'] for source in sources[:2]], 'acceptance_contract': contract}
    consumer = add_node(h, project, type='analysis', title='Purpose-gated use', config=config)
    consumed = h.launch(consumer)
    admitted = h.request('GET', f"/api/runs/{consumed['id']}/acceptance")
    if confirmation:
        current = admitted['confirmations'][0]
        assert current['comparison']['directly_comparable'], current
        assert current['verification_status'] == 'accepted' and current['numerical_coverage']['ready'], current
        assert current['goal_applicability']['ready'], current
        assert admitted['comparison']['evaluation_classification'] == 'exploratory'
    if confirmation in ('adaptive', 'legacy'):
        assert not admitted['ready'], admitted
        assert 'independent held-out evaluation' in admitted['failures'][0]
        assert_admission_blocks_worker(h, consumed)
        return
    assert admitted['ready'] and admitted['purpose'] == purpose, admitted
    assert h.terminal(consumed)['status'] == 'completed'
    if purpose == 'raw_data':
        actual = h.output(sources[0])/'workspace'/'data.csv'
        rows = actual.read_text().splitlines(); assert len(rows) == 501
        assert sum(int(row.rsplit(',', 1)[1]) for row in rows[1:]) == sum(i*i for i in range(500))
        (h.output(sources[0])/'workspace'/'units.json').write_text(json.dumps({'units': {'value': 'kg'}}))
    elif purpose == 'major_claim':
        h.request('PATCH', f"/api/runs/{sources[2]['id']}/configuration", json={'research_phase': 'selection'})
    else:
        h.request('PATCH', f"/api/runs/{sources[1]['id']}/configuration", json={
            'comparison': {**signature, 'budget': {'evaluations': 1000}}})
    denied = h.request('GET', f"/api/runs/{consumed['id']}/acceptance")
    assert not denied['ready'] and denied['failures'], denied
    assert_admission_blocks_worker(h, h.launch(consumer))
