"""Local inventory coverage across bounded reconciliation passes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def write_marker(root, relative, content='observed marker'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class Inventory:
    def __init__(self, client):
        from sqlalchemy import select
        from services.api.common import project_dir
        from services.api.db import Session
        from services.observation.files import sync_scopes
        from services.observation.models import ObservationScope
        from services.observation.reporting import ensure_state

        response = client.post('/api/projects', json={'name': 'Inventory fixture'})
        assert response.status_code == 200, response.text
        project = response.json()['id']
        self.root = project_dir(project)
        with Session.begin() as session:
            ensure_state(session, project)
            sync_scopes(session, project)
            self.scope_id = session.scalar(select(ObservationScope.id).where(
                ObservationScope.project_id == project, ObservationScope.kind == 'project'))

    def scan(self):
        from services.observation.files import reconcile

        reconcile(self.scope_id)
        return self.progress()

    def progress(self):
        from services.api.db import Session
        from services.observation.models import ObservationScope

        with Session() as session:
            scope = session.get(ObservationScope, self.scope_id)
            return scope.coverage, scope.scan_generation, scope.queue, scope.error

    def paths(self, state='available'):
        from sqlalchemy import select
        from services.api.db import Session
        from services.observation.models import ObservedFile

        with Session() as session:
            return set(session.scalars(select(ObservedFile.path).where(
                ObservedFile.scope_id == self.scope_id, ObservedFile.state == state)))

    def source(self, relative):
        from sqlalchemy import select
        from services.api.db import Session
        from services.observation.files import reference, resolve
        from services.observation.models import ObservationScope, ObservedFile

        with Session() as session:
            scope = session.get(ObservationScope, self.scope_id)
            row = session.scalar(select(ObservedFile).where(
                ObservedFile.scope_id == scope.id, ObservedFile.path == relative))
            return resolve(reference(row, scope))


def wide_directories(inventory):
    last = 'directory-2054/nested/marker.txt'
    expected = {f'directory-{index:04d}/marker.txt' for index in range(2054)} | {last}
    for relative in expected:
        write_marker(inventory.root, relative, relative)
    for _ in range(80):
        coverage, generation, pending, error = inventory.scan()
        assert len(pending) <= 2048
        if coverage == 'complete':
            break
    observed = inventory.paths()
    assert expected <= observed, f'{len(expected & observed)} of {len(expected)} marker files, scan {generation}: {error}'
    assert coverage == 'complete' and generation == 1 and not pending
    result = inventory.source(last)
    assert result.availability == 'available'
    assert result.content == last


def legacy_cursor(inventory):
    from services.api.db import Session
    from services.observation.files import observe
    from services.observation.models import ObservationScope

    expected = {'lost/marker.txt', 'pending/first.txt', 'pending/second.txt', 'tail/marker.txt'}
    for relative in expected:
        write_marker(inventory.root, relative)
    with Session.begin() as session:
        scope = session.get(ObservationScope, inventory.scope_id)
        scope.scan_generation = 7
        scope.coverage = 'partial'
        scope.error = 'Directory queue capacity reached; inventory is incomplete'
        scope.queue = [['', 'pending'], ['pending', 'first.txt']]
        observe(session, scope, 'pending/first.txt', 7)

    coverage, generation, pending, error = inventory.scan()
    assert coverage == 'partial' and generation == 7 and not pending and error
    assert expected - {'lost/marker.txt'} <= inventory.paths()
    assert 'lost/marker.txt' not in inventory.paths()
    coverage, generation, pending, error = inventory.scan()
    assert coverage == 'complete' and generation == 8 and not pending and not error
    assert expected <= inventory.paths()


def renamed_parent(inventory):
    first = 'omega/marker-000.txt'
    for index in range(250):
        write_marker(inventory.root, f'omega/marker-{index:03d}.txt')
    coverage, generation, pending, error = inventory.scan()
    assert coverage == 'partial' and generation == 1 and pending
    assert first in inventory.paths()
    (inventory.root / 'omega').rename(inventory.root / 'alpha')

    for _ in range(10):
        coverage, generation, pending, error = inventory.scan()
        if not pending:
            break
    assert coverage == 'partial' and generation == 1 and error
    assert first in inventory.paths()
    assert 'alpha/marker-000.txt' not in inventory.paths()
    for _ in range(10):
        coverage, generation, pending, error = inventory.scan()
        if coverage == 'complete':
            break
    assert coverage == 'complete' and generation == 2
    assert {f'alpha/marker-{index:03d}.txt' for index in range(250)} <= inventory.paths()
    assert first in inventory.paths('deleted')


def interrupted_pass(inventory):
    from unittest.mock import patch
    from services.observation import files

    relative = 'directory/marker.txt'
    write_marker(inventory.root, relative)
    progress = inventory.progress()
    observe = files.observe

    def interrupt(session, scope, path, generation):
        row = observe(session, scope, path, generation)
        if path == relative:
            session.flush()
            raise RuntimeError('Interrupted scan fixture')
        return row

    with patch.object(files, 'observe', interrupt), pytest.raises(RuntimeError, match='Interrupted scan fixture'):
        inventory.scan()
    assert inventory.progress() == progress
    assert relative not in inventory.paths()
    assert inventory.scan()[:2] == ('complete', 1)
    assert relative in inventory.paths()


@pytest.mark.parametrize('case', ['wide_directories', 'legacy_cursor', 'renamed_parent', 'interrupted_pass'])
def test_local_inventory_resumes(tmp_path, case):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'), 'FOREST_MODEL': '',
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'inventory.db'),
           'FOREST_OWNER_TOKEN': 'inventory-fixture-token', 'PYTHONPATH': str(root)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), case], cwd=root, env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from services.api.db import migrate
    from services.api.main import app

    migrate()
    globals()[sys.argv[1]](Inventory(TestClient(app)))
