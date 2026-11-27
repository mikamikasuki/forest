"""Compare the original recorded request with the actual inference child input."""
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from research.agents.schemas import json_action_schema, decode_tool_call

from research.agents.provider import ModelClient


def test_codex_receipt_preserves_actual_cli_prompt_and_effective_controls(monkeypatch):
    from research.agents import codex_transport
    from research.agents.policy import RESEARCH_POLICY_MARKER
    provider = {'id':'test-only', 'kind':'codex_cli', 'base_url':'http://127.0.0.1',
                'model':'gpt-6-luna', 'config':{'reasoning_effort':'xhigh'}}
    client = ModelClient(provider, request_guard=lambda event: None)
    messages = [{'role':'system','content':'Current controls'}, {'role':'user','content':'Owner correction 中文'},
                {'role':'assistant','content':'Previous public action'}]
    path, payload = client.build_request(messages)
    recorded = json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',', ':')).encode()
    captured = []
    def child(argv, **kwargs):
        captured.append(kwargs['input'])
        return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps(event) for event in [
            {'type':'item.completed','item':{'type':'agent_message','text':'{"tool":"finish","arguments":{}}'}},
            {'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5,'cached_input_tokens':0}}]), stderr='')
    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value:'/verified/test/codex')
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)
    client.complete(messages)
    assert path == 'codex_cli'
    assert captured[0].encode() == recorded
    effective = json.loads(captured[0])['messages']
    assert RESEARCH_POLICY_MARKER in effective[0]['content']
    assert effective[1:] == messages


def test_codex_action_schema_follows_effective_permissions(monkeypatch):
    from research.agents import codex_transport
    schema = json_action_schema(['read_file', 'finish'])
    client = ModelClient({'id': 'test-only', 'kind': 'codex_cli', 'base_url': 'http://127.0.0.1', 'model': 'gpt-6-luna',
                         'config': {'_forest_action_schema': schema}}, request_guard=lambda event: None)
    messages = [{'role': 'user', 'content': 'Review existing evidence'}]
    path, payload = client.build_request(messages)
    assert payload['response_schema'] == schema
    def child(argv, **kwargs):
        assert json.loads(Path(argv[argv.index('--output-schema') + 1]).read_text()) == schema
        assert json.loads(kwargs['input']) == payload
        assert schema['properties']['tool']['enum'] == ['read_file', 'finish']
        action = {'tool': 'read_file', 'arguments': {'path': 'actual.json', 'offset': None, 'limit': 16000}, 'summary': 'Inspect evidence'}
        decoded = decode_tool_call({'name': action['tool'], 'arguments': action['arguments'], 'call_id': None}, ['read_file', 'finish'])
        assert decoded['arguments'] == {'path': 'actual.json', 'limit': 16000}
        return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps(event) for event in [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(action)}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}]), stderr='')
    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value: '/verified/test/codex')
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)
    client.complete(messages)


@pytest.mark.parametrize('limit', [1, 7])
def test_codex_transport_applies_per_request_output_limit(monkeypatch, limit):
    from research.agents import codex_transport

    provider = {'id': 'test-only', 'kind': 'codex_cli', 'base_url': 'http://127.0.0.1',
                'model': 'gpt-6-luna', 'config': {'reasoning_effort': 'high'}}
    events = []
    client = ModelClient(provider, request_guard=lambda event: events.append(event) or len(events))
    messages = [{'role': 'user', 'content': 'Return the finish action'}]

    def child(argv, **kwargs):
        config_values = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == '--config']
        assert f'model_max_output_tokens={limit}' in config_values
        return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps(event) for event in [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"tool":"finish","arguments":{}}'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}},
        ]), stderr='')

    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value: '/verified/test/codex')
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)

    client.complete(messages, max_output_tokens=limit)

    assert events[0]['phase'] == 'before'
    assert events[0]['max_output_tokens'] == limit


_MISSING = object()


@pytest.mark.parametrize(
    'reported_reasoning,expected_reasoning',
    [(42, 42), (0, 0), (_MISSING, None)],
    ids=['reported', 'explicit-zero', 'absent-unknown'],
)
def test_codex_usage_preserves_reported_reasoning_tokens(monkeypatch, reported_reasoning, expected_reasoning):
    from research.agents import codex_transport

    provider = {'id': 'test-only', 'kind': 'codex_cli', 'base_url': 'http://127.0.0.1',
                'model': 'gpt-6-luna', 'config': {'reasoning_effort': 'high'}}
    events = []

    def record(event):
        events.append(event)
        return len(events)

    client = ModelClient(provider, request_guard=record)
    usage = {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 50}
    if reported_reasoning is not _MISSING:
        usage['reasoning_output_tokens'] = reported_reasoning

    def child(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps(event) for event in [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"tool":"finish","arguments":{}}'}},
            {'type': 'turn.completed', 'usage': usage},
        ]), stderr='')

    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value: '/verified/test/codex')
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)

    result = client.complete([{'role': 'user', 'content': 'Return the finish action'}])

    expected = {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 50,
                'reasoning_tokens': expected_reasoning, 'cost': None,
                'cost_source': 'codex_subscription_unpriced'}
    assert result['usage'] == expected
    assert events[-1]['usage'] == expected
