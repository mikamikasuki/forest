"""Overlapping Library imports use real HTTP handlers and database transactions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

ROOT = Path(__file__).resolve().parents[1]


def test_library_import_reuses_publication_during_concurrent_requests(tmp_path):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_DATABASE_URL': f'sqlite:///{tmp_path / "library.sqlite"}',
           'FOREST_OWNER_TOKEN': 'isolated-library-import-owner', 'FOREST_MODEL': '',
           'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr


def check_concurrent_import():
    from fastapi.testclient import TestClient
    from sqlalchemy import event, select
    from services.api.db import Event as ProjectEvent, Session, SourcePaper, SourcePassage, engine, migrate
    from services.api.main import app

    first_insert = Event()
    second_write = Event()
    first_connection = None

    def synchronize(connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal first_connection
        inserting = statement.startswith('INSERT INTO source_papers ')
        locking = statement == 'BEGIN IMMEDIATE' or (
            'FROM projects' in statement and 'FOR UPDATE' in statement)
        if inserting and first_connection is None:
            first_connection = connection
            first_insert.set()
            assert second_write.wait(8), 'Second request did not reach the database'
        elif (first_connection is not None and connection is not first_connection
              and (inserting or locking)):
            # The unpatched request reaches its own insert. A serialized request
            # attempts the write lock before it can see the first request's result.
            second_write.set()

    # Run the HTTP handlers with no unrelated observer transactions.
    migrate()
    with closing(TestClient(app)) as client:
        project = client.post('/api/projects', json={'name': 'Concurrent library imports',
                              'goal': 'Retain one source and one set of passages per DOI'})
        project.raise_for_status()
        project_id = project.json()['id']
        doi = '10.5555/concurrent-library-import'

        def submit(title):
            return client.post('/api/library/import', json={'project_id': project_id,
                               'paper': {'doi': doi, 'title': title,
                                         'passages': [{'text': f'Passage from {title}'}]}})

        event.listen(engine, 'before_cursor_execute', synchronize)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(submit, 'Original source')
                assert first_insert.wait(8), 'First request did not reach its Library insert'
                second = pool.submit(submit, 'Retry with separate metadata')
                responses = first.result(timeout=12), second.result(timeout=12)
        finally:
            event.remove(engine, 'before_cursor_execute', synchronize)

        for response in responses:
            response.raise_for_status()
        original, retry = [response.json() for response in responses]
        with Session() as session:
            papers = list(session.scalars(select(SourcePaper).where(SourcePaper.project_id == project_id)))
            passages = list(session.scalars(
                select(SourcePassage).where(SourcePassage.project_id == project_id)))
            events = list(session.scalars(select(ProjectEvent).where(ProjectEvent.project_id == project_id)))
        stored = [(paper.id, paper.data['doi']) for paper in papers]
        assert stored == [(original['id'], doi)], stored
        assert retry == original and original['title'] == 'Original source'
        assert [passage.data for passage in passages] == [
            {'paper_id': original['id'], 'text': 'Passage from Original source'}]
        assert [item.data for item in events if item.type == 'artifact_available'] == [
            {'kind': 'library', 'id': original['id']}]


if __name__ == '__main__':
    check_concurrent_import()
