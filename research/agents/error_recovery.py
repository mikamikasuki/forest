"""Persisted diagnosis circuit breaker for repeated Research Agent tool errors."""
from __future__ import annotations
from copy import deepcopy


DIAGNOSTIC_TOOLS = {
    'read_file', 'list_files', 'read_transcript', 'read_context_segment',
    'inspect_process', 'read_process_output', 'results',
}
SUBSTANTIVE_TOOLS = {
    'write_file', 'write_file_chunk', 'python', 'run_command', 'start_process',
    'experiment_run', 'verification_run', 'graph_command', 'paper_generate',
    'paper_compile', 'figure_render', 'theory_check',
}


def classify_failure(tool, result):
    """Classify only observed tool failures; never infer model-side success."""
    if result.get('action_discarded'):
        return None
    error = str(result.get('error') or result.get('reason') or '').strip()
    exit_code = result.get('exit_code')
    failed = bool(error) or (isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0) or result.get('status') == 'failed'
    if not failed:
        return None
    text = error.casefold()
    code = str(result.get('code') or result.get('error_code') or '').upper()
    if code in {'PATH_ESCAPE', 'PERMISSION_DENIED', 'EACCES', 'EPERM', 'AUTHORIZATION'} or any(
            token in text for token in ('permission denied', 'not authorized', 'authorization', 'unauthorized', 'forbidden', 'path escapes', 'access denied')):
        category = 'permission_error'
    elif code in {'BUDGET_EXHAUSTED', 'RESOURCE_BUDGET_EXHAUSTED', 'GPU_BUDGET_EXHAUSTED',
                  'MODEL_BUDGET_EXHAUSTED', 'TIME_BUDGET_EXHAUSTED'} or any(token in text for token in (
                    'budget exhausted', 'budget reached', 'spending limit cannot reserve',
                    'configured agent budget reached', 'hard resource limit reached')):
        category = 'budget_exhausted'
    elif any(token in text for token in ('provider', 'model response', 'model request', 'context window', 'incomplete response')):
        category = 'model_error'
    elif any(token in text for token in ('not found', 'no such file', 'no module named', 'missing dependency', 'executable',
                                          'missing project file', 'timed out', 'timeout', 'unavailable', 'environment')):
        category = 'environment_error'
    elif tool in {'run_command', 'python', 'start_process', 'experiment_run', 'verification_run'} and (
            exit_code not in (None, 0) or result.get('status') == 'failed'):
        category = 'experiment_failure'
    else:
        category = 'tool_error'
    return {'category': category, 'tool': tool, 'code': code or None, 'message': error or 'Tool returned a nonzero exit status'}


def action_signature(tool, arguments, basis=None):
    return {'tool': tool, 'arguments': deepcopy(arguments or {}), 'source_basis': deepcopy(basis)}


def rejected_action(state, tool, arguments):
    basis = state.get('action_source_basis') if tool in {'python', 'run_command', 'start_process'} else state.get('authorization_basis')
    signature = action_signature(tool, arguments, basis)
    recovery = state.get('error_recovery') or {}
    failure = next((item['failure'] for item in recovery.get('operation_failures', [])
                    if item['operation'] == signature and item.get('rejected')), None)
    if failure:
        return {'error': failure['message'], 'code': failure.get('code'),
                'exit_code': 1, 'repetition_prevented': True,
                'reason': 'Unchanged failed operation was already diagnosed; choose a corrected authorized action.'}
    return None


def observe_tool_result(state, tool, result, step, *, arguments=None):
    """Return a persisted event and whether the caller should diagnose or stop."""
    recovery = state.setdefault('error_recovery', {'last_category': None, 'repeat_count': 0,
        'diagnosis_pending': False, 'history': []})
    failure = classify_failure(tool, result)
    if failure is None:
        if recovery.get('diagnosis_pending') and (tool in DIAGNOSTIC_TOOLS or tool in SUBSTANTIVE_TOOLS):
            event = {'step': step, 'state': 'diagnosis_observed', 'tool': tool,
                     'prior_category': recovery.get('last_category')}
            diagnosis_operation = recovery.get('diagnosis_operation')
            if diagnosis_operation is None:
                diagnosis_event = next((item for item in reversed(recovery.get('history', []))
                                        if item.get('state') == 'diagnosis_required'), None)
                if diagnosis_event:
                    diagnosis_operation = diagnosis_event.get('operation')
            if event['prior_category'] == 'environment_error' and diagnosis_operation:
                operation = next((item for item in recovery.get('operation_failures', [])
                                  if item['operation'] == diagnosis_operation), None)
                if operation and operation.get('failure', {}).get('category') == 'environment_error':
                    operation['rejected'] = False
                    event['retry_unblocked'] = True
            recovery['diagnosis_pending'] = False
            recovery['diagnosis_operation'] = None
            recovery.setdefault('history', []).append(event)
            return event, 'continue'
        return None, 'continue'

    prior_category = recovery.get('last_category')
    if failure['category'] == 'budget_exhausted':
        event = {'step': step, **failure, 'repeat_count': recovery.get('repeat_count', 0) + 1,
                 'state': 'budget_exhausted'}
        recovery.update(last_category=failure['category'], repeat_count=event['repeat_count'],
                        diagnosis_pending=False, diagnosis_operation=None,
                        last_error=failure['message'], last_failure_tool=tool)
        recovery.setdefault('history', []).append(event)
        return event, 'block'
    count = recovery.get('repeat_count', 0) + 1 if prior_category == failure['category'] else 1
    basis = state.get('action_source_basis') if tool in {'python', 'run_command', 'start_process'} else state.get('authorization_basis')
    signature = action_signature(tool, arguments, basis)
    attempts = recovery.setdefault('operation_failures', [])
    operation = next((item for item in attempts if item['operation'] == signature), None)
    if operation is None:
        operation = {'operation': signature, 'count': 0}
        attempts.append(operation)
    operation['count'] += 1
    operation['failure'] = failure
    if failure['category'] == 'permission_error' or operation['count'] >= 2:
        operation['rejected'] = True
    outcome = 'block' if result.get('repetition_prevented') or operation['count'] >= 3 else 'diagnose' if count >= 2 else 'continue'
    event = {'step': step, **failure, 'repeat_count': count,
             'state': 'blocked_after_failed_diagnosis' if outcome == 'block' else 'diagnosis_required' if outcome == 'diagnose' else 'observed',
             'operation': signature, 'action_failures': operation['count']}
    recovery.update(last_category=failure['category'], repeat_count=count,
                    diagnosis_pending=outcome == 'diagnose', last_error=failure['message'],
                    last_failure_tool=tool,
                    diagnosis_operation=signature if outcome == 'diagnose' else None)
    recovery.setdefault('history', []).append(event)
    return event, outcome


def diagnosis_prompt(event):
    return ('ERROR RECOVERY REQUIRED: the same classified failure occurred twice. stop retrying the failing action. '
            f"Classification: {event['category']}; tool: {event['tool']}; observed error: {event['message']}. "
            'Inspect the actual source, permissions, environment, provider response, experiment log, or remaining budget '
            'using available diagnostic tools. State the evidence-based cause, then either make one materially corrected '
            'action or stop and report the blocker. Do not merely change arguments cosmetically, repeat the action, '
            'claim an experiment succeeded, or spend more compute without resolving the diagnosed cause.')
