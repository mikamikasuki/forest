import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from research.agents.output_budget import configure_request_policy, output_allocation, grow_output_allocation
from research.agents.provider import ModelClient, ProviderError, parse_response
from research.planning.model import context_model_json, _merge_complete_json


def _recorded_chunks(saved):
    recorded = []
    for message in saved['messages']:
        try:
            value = json.loads(message.get('content', ''))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get('status') == 'chunk_recorded':
            recorded.append(value)
    return recorded


def test_stage_complexity_and_provider_capacity_allocate_distinct_budgets():
    config = {'adaptive_output_fallback_tokens': 400,
              'adaptive_output_budget': {'complexity': 'high'},
              'model_capabilities': {'max_output_tokens': 12_000, 'context_window_tokens': 20_000}}
    messages = [{'role': 'user', 'content': 'x' * 3000}]
    concise = output_allocation(config, messages, stage='concise')
    paper = output_allocation(config, messages, stage='paper')
    assert concise['tokens'] == 800
    assert paper['tokens'] == 3200
    assert paper['tokens'] <= config['model_capabilities']['max_output_tokens']
    assert paper['tokens'] + paper['estimated_input_tokens'] <= config['model_capabilities']['context_window_tokens']


def test_project_and_run_policy_inheritance_preserves_verified_model_capabilities():
    class Client:
        config = {'model_capabilities': {'max_output_tokens': 8000, 'source': 'verified-provider'}}

    project_policy = {'adaptive_output_budget': {'stage_targets': {'paper': 5000}},
                      'adaptive_output_fallback_tokens': 300, 'task_complexity': 'high'}
    client = configure_request_policy(Client(), project_policy, 'paper')
    assert client.config['output_stage'] == 'paper'
    assert client.config['adaptive_output_budget']['stage_targets']['paper'] == 5000
    assert client.config['model_capabilities'] == {'max_output_tokens': 8000, 'source': 'verified-provider'}
    assert output_allocation(client.config, [{'role': 'user', 'content': 'task'}],
                             stage=client.config['output_stage'])['tokens'] == 5000


def test_unknown_capability_uses_configurable_fallback_and_does_not_invent_growth():
    config = {'adaptive_output_fallback_tokens': 300}
    allocation = output_allocation(config, [{'role': 'user', 'content': 'task'}], stage='research')
    assert allocation['tokens'] == 300
    assert allocation['source'] == 'conservative_fallback'
    assert grow_output_allocation(config, [], 300) is None


def test_declared_local_context_bounds_growth_when_provider_has_no_separate_output_limit():
    config = {'model_capabilities': {'context_window_tokens': 6000}}
    messages = [{'role': 'user', 'content': 'x' * 3000}]
    allocation = output_allocation(config, messages, stage='paper')
    grown = grow_output_allocation(config, messages, allocation['tokens'])
    assert grown is not None and grown > allocation['tokens']
    assert grown + allocation['estimated_input_tokens'] <= 6000


def test_growth_is_bounded_by_declared_model_and_remaining_context():
    config = {'model_capabilities': {'max_output_tokens': 9000, 'context_window_tokens': 5000},
              'adaptive_output_budget': {'growth_factor': 2}}
    messages = [{'role': 'user', 'content': 'x' * 3000}]
    current = output_allocation(config, messages, requested=1000)['tokens']
    next_budget = grow_output_allocation(config, messages, current)
    assert current == 1000
    assert next_budget > current
    assert next_budget <= 5000 - output_allocation(config, messages)['estimated_input_tokens']


def test_explicit_ollama_context_is_a_hard_output_boundary():
    config = {'context_length': 3000, 'adaptive_output_fallback_tokens': 5000}
    messages = [{'role': 'user', 'content': 'x' * 3000}]
    allocation = output_allocation(config, messages)
    assert allocation['tokens'] + allocation['estimated_input_tokens'] <= 3000
    assert grow_output_allocation(config, messages, allocation['tokens']) is None


def test_ollama_model_metadata_clamps_an_overlarge_configured_context():
    class Metadata(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            body=json.dumps({'model_info': {'fixture.context_length': 6000}}).encode()
            self.send_response(200); self.send_header('Content-Length',str(len(body)))
            self.end_headers(); self.wfile.write(body)

    server=ThreadingHTTPServer(('127.0.0.1',0),Metadata)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        client=ModelClient({'kind':'ollama','base_url':f'http://127.0.0.1:{server.server_port}',
            'model':'fixture','config':{'context_length':10000}})
        client._discover_local_capabilities()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert client.config['context_length']==6000
    assert client.config['model_capabilities']['context_window_tokens']==6000


def test_provider_payload_uses_request_budget_and_ollama_context_only_when_declared():
    ollama = ModelClient({'kind': 'ollama', 'base_url': 'http://localhost:11434', 'model': 'local',
                          'config': {'adaptive_output_fallback_tokens': 500}})
    endpoint, payload = ollama.build_request([{'role': 'user', 'content': 'task'}], max_output_tokens=321)
    assert endpoint == '/api/chat'
    assert payload['options']['num_predict'] == 321
    assert 'num_ctx' not in payload['options']
    declared = ModelClient({'kind': 'ollama', 'base_url': 'http://localhost:11434', 'model': 'local',
                            'config': {'model_capabilities': {'context_window_tokens': 4096}}})
    _, payload = declared.build_request([{'role': 'user', 'content': 'task'}], max_output_tokens=321)
    assert payload['options']['num_ctx'] == 4096


def test_actual_truncation_retries_within_declared_cap_and_aggregates_usage():
    received=[]
    class Transport(BaseHTTPRequestHandler):
        def log_message(self,*_args): pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(payload['max_output_tokens'])
            first=len(received)==1
            response=({'id':'truncated-1','status':'incomplete',
                'incomplete_details':{'reason':'max_output_tokens'},
                'usage':{'input_tokens':50,'output_tokens':900},
                'output':[{'type':'message','status':'incomplete','content':[{'type':'output_text','text':'{"partial":'}]}]}
                if first else {'id':'complete-2','status':'completed',
                    'usage':{'input_tokens':60,'output_tokens':100},
                    'output':[{'type':'message','status':'completed','content':[{'type':'output_text','text':'{"ok":true}'}]}]})
            body=json.dumps(response).encode(); self.send_response(200); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Transport)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        events=[]
        client=ModelClient({'kind':'openai','base_url':f'http://127.0.0.1:{server.server_port}/v1',
            'model':'declared-capability-fixture','config':{'api':'responses','max_output_tokens':1000,
            'max_retries':0,'output_recovery_attempts':2,'model_capabilities':{
                'max_output_tokens':4000,'context_window_tokens':10000}}},request_guard=lambda event: events.append(event) or len(events))
        result=client.complete([{'role':'user','content':'Return a complete JSON object.'}])
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert received==[1000,2000]
    assert result['text']=='{"ok":true}'
    assert result['usage']['input_tokens']==110 and result['usage']['output_tokens']==1000
    assert result['output_recovery']['request_count']==2
    assert [event['max_output_tokens'] for event in events if event['phase']=='before']==[1000,2000]


def test_incomplete_json_is_never_accepted_as_a_complete_response():
    value = {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
             'usage': {'input_tokens': 50, 'output_tokens': 20},
             'output': [{'type': 'message', 'status': 'incomplete', 'content': [
                 {'type': 'output_text', 'text': '{"ideas":[{"id":"partial"}'}]}]}
    with pytest.raises(ProviderError) as caught:
        parse_response(value, 'responses')
    assert caught.value.incomplete_reason == 'max_output_tokens'
    assert caught.value.partial_output


def test_output_allocation_metadata_is_json_serializable_for_audit():
    allocation = output_allocation({'model_capabilities': {'max_output_tokens': 1000}},
                                   [{'role': 'user', 'content': 'audit'}], stage='analysis')
    assert json.loads(json.dumps(allocation)) == allocation


def test_complete_json_subdocuments_merge_and_conflicts_are_rejected():
    assert _merge_complete_json([{'ideas': [{'id': 'a'}]}, {'ideas': [{'id': 'b'}]}]) == {
        'ideas': [{'id': 'a'}, {'id': 'b'}]}
    with pytest.raises(ValueError, match='conflict'):
        _merge_complete_json([{'status': 'complete'}, {'status': 'partial'}])


def test_managed_planner_splits_and_merges_only_complete_json_chunks(tmp_path):
    class Provider:
        api = 'chat_completions'
        native_tools = False
        config = {'working_context_chars': 2500, 'adaptive_output_fallback_tokens': 256}

        def __init__(self):
            self.calls = 0
            self.split_started = False

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1 and not self.split_started:
                self.split_started = True
                raise ProviderError('provider truncated JSON', code='incomplete_response',
                    incomplete_reason='max_output_tokens', usage={'input_tokens': 100, 'output_tokens': 256,
                    'cached_input_tokens': 0, 'reasoning_tokens': 0, 'cost': None},
                    requested_output_tokens=256)
            index = self.calls - 2
            envelope = {'_forest_chunk': {'index': index, 'total': 2},
                        'content': {'ideas': [{'id': chr(ord('a') + index)}]}}
            text = json.dumps({'tool': 'submit_result', 'arguments': {'result_json': json.dumps(envelope)}})
            return {'text': text, 'usage': {'input_tokens': 100, 'output_tokens': 40,
                    'cached_input_tokens': 0, 'reasoning_tokens': 0, 'cost': None},
                    'model': 'contract-model', 'request_id': f'req-{self.calls}', 'elapsed': 0.01,
                    'tool_calls': [], 'output': []}

    provider = Provider()
    proposal, aggregate = context_model_json(provider,
        [{'role': 'system', 'content': 'Return the complete ideas JSON.'},
         {'role': 'user', 'content': 'x' * 5000}], tmp_path / 'planner', char_hint=2500)
    assert proposal == {'ideas': [{'id': 'a'}, {'id': 'b'}]}
    assert json.loads(aggregate['text']) == proposal
    assert provider.calls == 3
    session = Path(aggregate['context_session_path'])
    saved = json.loads((session / 'session.json').read_text())
    recorded = _recorded_chunks(saved)
    assert recorded[0]['missing_indices'] == [1]
    assert recorded[0]['missing_count'] == 1
    assert recorded[0]['missing_indices_truncated'] is False


def _split_response(native, envelope, call_id):
    arguments = {'result_json': json.dumps(envelope)}
    if native:
        function_call = {'type': 'function_call', 'name': 'submit_result',
                         'arguments': json.dumps(arguments), 'call_id': call_id}
        return {'text': '', 'tool_calls': [{'name': 'submit_result', 'arguments': arguments, 'call_id': call_id}],
                'output': [function_call], 'model': 'offline-split',
                'usage': {'input_tokens': 1, 'output_tokens': 1, 'cost': None}, 'elapsed': 0}
    return {'text': json.dumps({'tool': 'submit_result', 'arguments': arguments}),
            'tool_calls': [], 'output': [], 'model': 'offline-split',
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'cost': None}, 'elapsed': 0}


class _BoundedSplitProvider:
    api = 'responses'
    config = {}

    def __init__(self, native, actions):
        self.native_tools = native
        self.actions = list(actions)
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ProviderError('offline output truncation', code='incomplete_response',
                                incomplete_reason='max_output_tokens', request_id='direct-truncation')
        action = self.actions[self.calls - 2]
        if isinstance(action, BaseException):
            raise action
        return _split_response(self.native_tools, action, f'chunk-call-{self.calls}')


@pytest.mark.parametrize('native', [False, True])
def test_output_split_accepts_two_to_max_chunks_and_merges_out_of_order(tmp_path, native):
    actions = [
        {'_forest_chunk': {'index': index, 'total': 64}, 'content': {'parts': [index]}}
        for index in [1, 0, *range(2, 64)]
    ]
    provider = _BoundedSplitProvider(native, actions)
    proposal, aggregate = context_model_json(
        provider,
        [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'Return split JSON.'}],
        tmp_path / ('native' if native else 'json'),
    )
    assert proposal == {'parts': list(range(64))}
    session = Path(aggregate['context_session_path'])
    saved = json.loads((session / 'session.json').read_text())
    assert saved['output_split_recovery']['assembled'] is True
    assert saved['output_split_recovery']['merged_indices'] == list(range(64))


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('total', [65, 500000])
def test_output_split_rejects_oversized_total_before_range_or_state_mutation(tmp_path, monkeypatch, native, total):
    original_range = context_model_json.__globals__.get('range', range)

    def guarded_range(stop, *args):
        if isinstance(stop, int) and stop > 64:
            raise AssertionError('oversized chunk total reached enumeration')
        return original_range(stop, *args)

    monkeypatch.setitem(context_model_json.__globals__, 'range', guarded_range)
    invalid = {'_forest_chunk': {'index': 0, 'total': total}, 'content': {'parts': [0]}}
    provider = _BoundedSplitProvider(native, [invalid, invalid, invalid])
    with pytest.raises(ValueError, match='Planner repeatedly') as caught:
        context_model_json(
            provider,
            [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'Return split JSON.'}],
            tmp_path / ('native' if native else 'json'),
        )
    assert 'total from 2 to 64' in str(caught.value.__cause__)
    session = next((tmp_path / ('native' if native else 'json') / 'context_sessions').iterdir())
    saved = json.loads((session / 'session.json').read_text())
    assert saved['output_split_recovery']['total'] is None
    assert saved['output_split_recovery']['received'] == {}
    assert saved['output_split_recovery']['pending'] is True


@pytest.mark.parametrize('native', [False, True])
def test_bad_changed_total_does_not_corrupt_existing_output_assembly(tmp_path, native):
    valid = {'_forest_chunk': {'index': 0, 'total': 64}, 'content': {'parts': [0]}}
    invalid = {'_forest_chunk': {'index': 1, 'total': 65}, 'content': {'parts': [1]}}
    provider = _BoundedSplitProvider(native, [
        valid, invalid, invalid, invalid,
    ])
    with pytest.raises(ValueError, match='Planner repeatedly') as caught:
        context_model_json(
            provider,
            [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': 'Return split JSON.'}],
            tmp_path / ('native' if native else 'json'),
        )
    assert 'total from 2 to 64' in str(caught.value.__cause__)
    session = next((tmp_path / ('native' if native else 'json') / 'context_sessions').iterdir())
    saved = json.loads((session / 'session.json').read_text())
    split = saved['output_split_recovery']
    assert split['total'] == 64
    assert split['received'] == {'0': {'parts': [0]}}
    recorded = _recorded_chunks(saved)
    assert recorded[-1]['missing_indices'] == list(range(1, 17))
    assert recorded[-1]['missing_count'] == 63
    assert recorded[-1]['missing_indices_truncated'] is True
    assert recorded[-1]['next_required_index'] == 1


def test_truncated_direct_paper_response_restarts_as_audited_json_chunks(tmp_path):
    from research.agents.context_store import ContextStore

    class Provider:
        api = 'chat_completions'
        native_tools = False
        config = {'working_context_chars': 24_000}

        def __init__(self):
            self.calls = 0

        def complete(self, messages, json_mode=True, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError('generation reached declared output limit', code='incomplete_response',
                    incomplete_reason='max_output_tokens', usage=None, request_id='partial-paper')
            session = next((tmp_path / 'paper' / 'context_sessions').iterdir())
            pending = ContextStore(session).pending()
            if pending:
                action = {'tool': 'read_context_segment', 'arguments': {
                    'segment_id': pending['segment_id'], 'offset': pending['offset'], 'limit': 6000, 'notes': 'read'}}
            else:
                index = self.calls - 3
                envelope = {'_forest_chunk': {'index': index, 'total': 2},
                            'content': {'sections': [{'id': str(index)}]}}
                action = {'tool': 'submit_result', 'arguments': {'result_json': json.dumps(envelope)}}
            return {'text': json.dumps(action), 'usage': {'input_tokens': 40, 'output_tokens': 20,
                    'cached_input_tokens': 0, 'reasoning_tokens': 0, 'cost': None},
                    'model': 'paper-contract', 'request_id': f'req-{self.calls}', 'elapsed': 0.01}

    provider = Provider()
    _, aggregate = context_model_json(provider,
        [{'role': 'system', 'content': 'Return a complete paper JSON object.'},
         {'role': 'user', 'content': 'Write a complete paper.'}], tmp_path / 'paper', parse_result=False,
        output_stage='paper')
    assert json.loads(aggregate['text']) == {'sections': [{'id': '0'}, {'id': '1'}]}
    assert aggregate['model_requests'] == 4
    session = Path(aggregate['context_session_path'])
    saved = json.loads((session / 'session.json').read_text())
    assert saved['output_split_recovery']['assembled'] is True
    assert saved['model_requests'] == 4
