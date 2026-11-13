import json
import os
import subprocess
import sys

from research.agents.error_recovery import (
    classify_failure,
    diagnosis_prompt,
    observe_tool_result,
    rejected_action,
)


def test_failure_classification_separates_permissions_environment_model_experiment_and_budget():
    cases = [
        ('read_file', {'error': 'Absolute file access requires an explicit read-only project authorization', 'exit_code': 1}, 'permission_error'),
        ('python', {'error': 'ModuleNotFoundError: No module named scipy', 'exit_code': 1}, 'environment_error'),
        ('experiment_run', {'error': 'Provider returned an incomplete response', 'exit_code': 1}, 'model_error'),
        ('run_command', {'error': 'run exited with status 1', 'exit_code': 1}, 'experiment_failure'),
        ('experiment_run', {'error': 'Configured GPU budget exhausted', 'exit_code': 1}, 'budget_exhausted'),
    ]
    assert [classify_failure(tool, result)['category'] for tool, result, _ in cases] == [expected for _, _, expected in cases]


def test_second_same_class_failure_requires_diagnosis_and_persists_audit():
    state = {'transcript': []}
    failure = {'error': 'Absolute file access requires an explicit read-only project authorization',
               'exit_code': 1, 'control_acceptance': {'accepted_at': 'different each retry'}}
    first, action = observe_tool_result(state, 'read_file', failure, 1)
    assert first['repeat_count'] == 1 and action == 'continue'
    second, action = observe_tool_result(state, 'read_file', failure, 2)
    assert second['repeat_count'] == 2 and action == 'diagnose'
    assert 'stop retrying' in diagnosis_prompt(second)
    persisted = json.loads(json.dumps(state))
    third, action = observe_tool_result(persisted, 'read_file', failure, 3)
    assert third['state'] == 'blocked_after_failed_diagnosis' and action == 'block'
    assert persisted['error_recovery']['history'][-1]['category'] == 'permission_error'


def test_diagnostic_reads_do_not_erase_repeated_failed_operation():
    state = {}
    failure = {'error': 'Execution denied by policy', 'exit_code': 1}
    observe_tool_result(state, 'run_command', failure, 1)
    _, action = observe_tool_result(state, 'run_command', failure, 2)
    assert action == 'diagnose'
    event, action = observe_tool_result(state, 'read_file', {'content': 'observed policy', 'exit_code': 0}, 3)
    assert action == 'continue' and event['state'] == 'diagnosis_observed'
    repeat, action = observe_tool_result(state, 'run_command', failure, 4)
    assert repeat['repeat_count'] == 3 and action == 'block'


def _environment_error_after_diagnosis():
    state = {}
    arguments = {'command': 'python -c "import optional_dependency"'}
    failure = {'error': "ModuleNotFoundError: No module named 'optional_dependency'", 'exit_code': 1}
    observe_tool_result(state, 'python', failure, 1, arguments=arguments)
    _, action = observe_tool_result(state, 'python', failure, 2, arguments=arguments)
    assert action == 'diagnose'
    return state, arguments, failure


def test_successful_environment_diagnosis_allows_the_same_operation_to_retry(tmp_path):
    state = {}
    command = [sys.executable, '-c', 'import forest_recovery_dependency_430']
    arguments = {'code': 'import forest_recovery_dependency_430'}
    environment = {**os.environ, 'PYTHONPATH': str(tmp_path / 'before-repair')}

    first = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)
    assert first.returncode != 0
    failure = {'error': first.stderr, 'exit_code': first.returncode}
    observe_tool_result(state, 'python', failure, 1, arguments=arguments)
    _, action = observe_tool_result(state, 'python', failure, 2, arguments=arguments)
    assert action == 'diagnose'
    assert rejected_action(state, 'python', arguments)['repetition_prevented'] is True

    dependency = tmp_path / 'forest_recovery_dependency_430.py'
    dependency.write_text("VALUE = 'available'\n", encoding='utf-8')
    repaired_environment = {**environment, 'PYTHONPATH': str(tmp_path)}
    diagnosis = subprocess.run(
        [sys.executable, '-c', 'import forest_recovery_dependency_430'],
        env=repaired_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert diagnosis.returncode == 0
    event, action = observe_tool_result(
        state, 'python', {'stdout': diagnosis.stdout, 'exit_code': diagnosis.returncode}, 3,
        arguments={'code': 'import forest_recovery_dependency_430; print("diagnosis")'},
    )
    assert action == 'continue' and event['retry_unblocked'] is True
    assert rejected_action(state, 'python', arguments) is None

    retry = subprocess.run(command, env=repaired_environment, capture_output=True, text=True, check=False)
    assert retry.returncode == 0
    event, action = observe_tool_result(
        state, 'python', {'stdout': retry.stdout, 'exit_code': retry.returncode}, 4,
        arguments=arguments,
    )
    assert event is None and action == 'continue'
    assert rejected_action(state, 'python', arguments) is None


def test_environment_operation_is_blocked_if_its_post_diagnosis_retry_still_fails():
    state, arguments, failure = _environment_error_after_diagnosis()
    observe_tool_result(
        state, 'read_file', {'content': 'dependency still missing', 'exit_code': 0}, 3
    )

    event, action = observe_tool_result(state, 'python', failure, 4, arguments=arguments)
    assert event['action_failures'] == 3 and action == 'block'
    assert rejected_action(state, 'python', arguments)['repetition_prevented'] is True


def test_persisted_pending_environment_diagnosis_can_resume_after_upgrade():
    state, arguments, _ = _environment_error_after_diagnosis()
    state['error_recovery'].pop('diagnosis_operation')

    event, action = observe_tool_result(
        state, 'read_file', {'content': 'dependency is available', 'exit_code': 0}, 3
    )
    assert action == 'continue' and event['retry_unblocked'] is True
    assert rejected_action(state, 'python', arguments) is None


def test_budget_filename_does_not_masquerade_as_resource_exhaustion():
    assert classify_failure('read_file', {'error': 'Missing project file: budget_protocol.json', 'exit_code': 1})['category'] == 'environment_error'
    assert classify_failure('python', {'error': 'No such file: token_limit.csv', 'exit_code': 1})['category'] == 'environment_error'
    assert classify_failure('run_command', {'error': 'Hard GPU budget exhausted', 'exit_code': 1})['category'] == 'budget_exhausted'
