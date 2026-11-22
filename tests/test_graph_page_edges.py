"""Graph paging preserves incident edges without loading unrelated edges."""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_graph_page_loads_only_incident_edges(tmp_path):
    env = {
        'PATH': os.defpath,
        'HOME': str(tmp_path),
        'TMPDIR': str(tmp_path),
        'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'graph.db'),
        'FOREST_DATA_DIR': str(tmp_path / 'data'),
        'FOREST_MODEL': '',
        'FOREST_OWNER_TOKEN': '',
        'PYTHONPATH': str(ROOT),
    }
    result = subprocess.run([sys.executable, __file__, 'run'], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    import sqlite3
    from fastapi.testclient import TestClient
    from sqlalchemy import event
    from services.api.db import Edge, Node, Project, Session, engine
    from services.api.main import app

    with TestClient(app) as client:
        with Session.begin() as session:
            session.add_all([Project(id='p', name='Page'), Project(id='other', name='Other')])
            session.flush()
            session.add_all([
                Node(id=f'n{i:03}', project_id='p', branch_id='a' if i < 2 else 'b',
                     title=f'Node {i:03}', created_at=f'{i:04}') for i in range(500)
            ])
            session.add(Node(id='other-node', project_id='other', branch_id='other', title='Other'))
            session.add_all([
                Edge(id='out', project_id='p', source='n000', target='n002'),
                Edge(id='in', project_id='p', source='n003', target='n000'),
                Edge(id='internal', project_id='p', source='n000', target='n001'),
                Edge(id='self', project_id='p', source='n001', target='n001'),
                Edge(id='foreign', project_id='other', source='n000', target='other-node'),
                *[Edge(id=f'u{i:03}', project_id='p', source='n002', target='n003') for i in range(200)],
            ])

        loaded = []
        def record_load(edge, _context):
            loaded.append(edge.id)
        event.listen(Edge, 'load', record_load)
        try:
            page = client.get('/api/projects/p/graph/page', params={'limit': 2}).json()
            assert [n['id'] for n in page['nodes']] == ['n000', 'n001']
            expected = {'out', 'in', 'internal', 'self'}
            page_edge_ids = [edge['id'] for edge in page['edges']]
            assert len(page_edge_ids) == 4 and set(page_edge_ids) == expected
            assert page['next_offset'] == 2
            assert len(loaded) == 4 and set(loaded) == expected, f'unrelated edges loaded: {len(loaded)}'

            loaded.clear()
            filtered = client.get('/api/projects/p/graph/page', params={'branch_id': 'a', 'query': 'Node 001'}).json()
            assert [n['id'] for n in filtered['nodes']] == ['n001']
            filtered_ids = [edge['id'] for edge in filtered['edges']]
            assert len(filtered_ids) == 2 and set(filtered_ids) == {'internal', 'self'}
            assert len(loaded) == 2 and set(loaded) == {'internal', 'self'}

            loaded.clear()
            empty = client.get('/api/projects/p/graph/page', params={'offset': 999}).json()
            assert empty['nodes'] == [] and empty['edges'] == [] and empty['next_offset'] is None
            assert loaded == []

            def limit_sql_variables(connection, _record, _proxy):
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            event.listen(engine, 'checkout', limit_sql_variables)
            try:
                full = client.get('/api/projects/p/graph/page', params={'limit': 500})
                assert full.status_code == 200, full.text
                assert len(full.json()['nodes']) == 500
                assert len(full.json()['edges']) == 204
            finally:
                event.remove(engine, 'checkout', limit_sql_variables)
        finally:
            event.remove(Edge, 'load', record_load)
