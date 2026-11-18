"""Remote environment regressions without contacting an SSH host or provider."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
CASES = ('task_only', 'absent', 'empty', 'null', 'overrides')


def environment_case(case, directory):
    # Settings and SQLAlchemy are first imported in this isolated interpreter.
    from services.api.db import migrate
    from services.worker.execute import local_command
    from runners.remote import execute_remote
    from runners.container import execute_container

    migrate()
    workspace, output = directory / 'workspace', directory / 'output'
    workspace.mkdir()
    output.mkdir()
    os.environ.update(FOREST_RUN_ID='worker-run', FOREST_ATTEMPT_ID='worker-attempt',
                      FOREST_HOST_ONLY_CANARY='must-not-be-forwarded')
    task = {'FOREST_TASK_ENV_CANARY': 'configured-at-task-level'}
    config = {'command': ['true'], 'remote': {'hostname': 'example.invalid',
              'username': 'test', 'workdir': '/remote/work'}}
    expected = {}
    if case in ('task_only', 'overrides'):
        config['env'] = dict(task)
        expected.update(task)
    if case in ('absent', 'null'):
        config['remote']['env'] = {'REMOTE_ONLY': 'remote-value'}
        expected['REMOTE_ONLY'] = 'remote-value'
    if case == 'null':
        config['env'] = None
    if case == 'empty':
        config['env'] = {}
        config['remote']['env'] = {}
    if case == 'overrides':
        identities = {'FOREST_RUN_ID': 'configured-run',
                      'FOREST_ATTEMPT_ID': 'configured-attempt',
                      'FOREST_RUN_DIR': '/configured/directory'}
        config['env'].update(identities, SHARED_SETTING='task-value')
        config['remote']['env'] = {**identities, 'SHARED_SETTING': 'remote-value',
                                   'REMOTE_ONLY': 'remote-value'}
        expected.update(SHARED_SETTING='remote-value', REMOTE_ONLY='remote-value')
    expected.update(FOREST_RUN_ID='worker-run', FOREST_ATTEMPT_ID='worker-attempt',
                    FOREST_RUN_DIR='/remote/work/worker-run')
    original = json.loads(json.dumps(config))
    launches, uploads = [], []

    class RecordingRemote:
        def __init__(self, remote):
            assert remote == config['remote']

        def upload_workspace(self, local, remote):
            uploads.append((local, remote))

        def start(self, argv, workdir, **kwargs):
            launches.append((argv, workdir, kwargs))
            return {'task_id': kwargs['task_id'], 'folder': workdir + '/.forest-tasks/' + kwargs['task_id']}

        def status(self, job):
            return {'status': 'completed', 'exit_code': 0}

        def output(self, job, offset):
            return {'text': '', 'offset': offset}

        def observe(self, job, cursor):
            return {'files': [], 'cursor': cursor, 'has_more': False}

    with patch('runners.remote.RemoteRunner', RecordingRemote):
        result = execute_remote(config, workspace, output)
        assert result['command_exit_code'] == 0
        assert len(launches) == 1
        argv, workdir, launch = launches[0]
        assert argv == ['true'] and workdir == expected['FOREST_RUN_DIR']
        assert launch['task_id'] == 'worker-run'
        assert launch['env'] == expected
        assert uploads == [(workspace, workdir)]
        assert config == original
        # A reconnect consumes the saved handle, not a second launch/environment.
        assert execute_remote(config, workspace, output)['command_exit_code'] == 0
        assert len(launches) == len(uploads) == 1

    if case == 'task_only':
        # Compare with an actual local child and the container adapter handoff.
        command = [sys.executable, '-c', "import os,pathlib; pathlib.Path('local-env.txt').write_text(os.environ['FOREST_TASK_ENV_CANARY'])"]
        assert local_command(command, workspace, output, config).returncode == 0
        assert (workspace / 'local-env.txt').read_text() == task['FOREST_TASK_ENV_CANARY']
        container_env = []

        def container_start(self, argv, workspace, **kwargs):
            container_env.append(kwargs['env'])
            return {'task_id': kwargs['task_id'], 'workspace': str(workspace)}

        with patch('runners.container.ContainerRunner.start', container_start), \
             patch('runners.container.ContainerRunner.status', return_value={'status': 'completed', 'exit_code': 0}), \
             patch('runners.container.ContainerRunner.output', return_value={'text': '', 'cursor': None}):
            assert execute_container({'command': ['true'], 'env': task}, workspace, output)['command_exit_code'] == 0
        assert container_env == [{**task, 'FOREST_RUN_ID': 'worker-run', 'FOREST_ATTEMPT_ID': 'worker-attempt'}]


@pytest.mark.parametrize('case', CASES)
def test_remote_environment_handoff(tmp_path, case):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'isolated.db'),
           'FOREST_MODEL': '', 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), case, str(tmp_path)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['command', 'experiment'])
def test_remote_environment_through_actual_worker(tmp_path, kind):
    """Real API, DB, worker and supervisor; only SSH/SCP transport runs locally."""
    from test_worker import Harness

    binary = tmp_path / 'transport'
    binary.mkdir()
    ssh = binary / 'ssh'
    ssh.write_text('#!' + sys.executable + '\n'
                   'import os,shlex,subprocess,sys\n'
                   "env={key:value for key,value in os.environ.items() if not key.startswith('FOREST_')}\n"
                   'sys.exit(subprocess.run(shlex.split(sys.argv[-1]),env=env).returncode)\n')
    scp = binary / 'scp'
    scp.write_text('#!' + sys.executable + '\n'
                   'import shutil,sys\n'
                   "shutil.copyfile(sys.argv[-2].split(':',1)[1],sys.argv[-1])\n")
    ssh.chmod(0o700)
    scp.chmod(0o700)
    harness = Harness(tmp_path)
    harness.env['PATH'] = str(binary) + os.pathsep + harness.env['PATH']
    remote_root = tmp_path / 'remote'
    try:
        harness.start_api()
        harness.start_worker()
        project, node = harness.project_node()
        script = "import json,os,pathlib; keys=['FOREST_TASK_ENV_CANARY','SHARED_SETTING','REMOTE_ONLY','FOREST_RUN_ID','FOREST_ATTEMPT_ID','FOREST_RUN_DIR']; pathlib.Path('metrics.json').write_text(json.dumps({key:os.getenv(key) for key in keys}))"
        configured_identity = {'FOREST_RUN_ID': 'wrong-run', 'FOREST_ATTEMPT_ID': 'wrong-attempt',
                               'FOREST_RUN_DIR': '/wrong/directory'}
        graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
            'request_id': kind + '-environment', 'expected_revision': graph['revision'],
            'operation': 'edit_node', 'targets': [node['id']], 'params': {'config': {
                'kind': kind, 'command': [sys.executable, '-c', script], 'timeout': 10,
                'env': {**configured_identity, 'FOREST_TASK_ENV_CANARY': 'task-value', 'SHARED_SETTING': 'task-value'},
                'remote': {'hostname': 'example.invalid', 'username': 'test', 'workdir': str(remote_root),
                           'env': {**configured_identity, 'SHARED_SETTING': 'remote-value', 'REMOTE_ONLY': 'remote-value'},
                           'outputs': ['metrics.json']}}}})
        run = harness.launch(node)
        completed = harness.terminal(run)
        assert completed['status'] == 'completed', completed
        output = harness.output(run)
        receipt = json.loads((output / 'remote_task.json').read_text())
        expected = {'FOREST_TASK_ENV_CANARY': 'task-value', 'SHARED_SETTING': 'remote-value',
                    'REMOTE_ONLY': 'remote-value', 'FOREST_RUN_ID': run['id'],
                    'FOREST_ATTEMPT_ID': completed['config']['execution_attempt']['id'],
                    'FOREST_RUN_DIR': str(remote_root / run['id'])}
        assert json.loads((output / 'workspace' / 'metrics.json').read_text()) == expected
        assert all(completed['metrics'][key] == value for key, value in expected.items())
        assert receipt['task_id'] == run['id'] and receipt['workdir'] == expected['FOREST_RUN_DIR']
        assert json.loads((Path(receipt['folder']) / 'state.json').read_text())['status'] == 'completed'
    finally:
        harness.cleanup()


if __name__ == '__main__':
    assert sys.argv[1] in CASES
    environment_case(sys.argv[1], Path(sys.argv[2]))
