"""Bounded CSV and TSV metadata contracts, including omitted-column disclosure."""

import json
import pytest

from services.observation.files import regular_read


@pytest.mark.parametrize('suffix,delimiter', [('.csv', ','), ('.tsv', '\t')])
@pytest.mark.parametrize('column_count', [99, 100, 101])
def test_delimited_sample_reports_total_columns_and_field_cap(tmp_path, suffix, delimiter, column_count):
    root = tmp_path / 'workspace'
    root.mkdir()
    columns = [f'field_{index:03d}' for index in range(column_count)]
    row = delimiter.join('value' for _ in columns)
    payload = (delimiter.join(columns) + '\n' + (row + '\n') * 500).encode()
    path = root / f'observed{suffix}'
    path.write_bytes(payload)

    data, envelope = regular_read(root, path.name)

    metadata = envelope['metadata']
    assert data is None
    assert metadata['sample_only'] is True
    assert metadata['sample_bytes'] <= 32768
    assert metadata['total_columns'] == column_count
    assert len(metadata['columns']) == min(column_count, 100)
    assert all(len(column) <= 240 for column in metadata['columns'])
    assert metadata['columns_truncated'] is (column_count > 100)
    assert len(metadata['sample_rows']) <= 10


@pytest.mark.parametrize('suffix,delimiter', [('.csv', ','), ('.tsv', '\t')])
def test_incomplete_sampled_header_has_unknown_total_and_explicit_omission(tmp_path, suffix, delimiter):
    root = tmp_path / 'workspace'
    root.mkdir()
    # The first logical record stays open past the bounded prefix.
    prefix = f'"open{delimiter}header\n'.encode()
    path = root / f'incomplete{suffix}'
    path.write_bytes(prefix + b'x' * (32768 - len(prefix) - 1) + b'\n' + b'x' * 262144)

    data, envelope = regular_read(root, path.name)

    metadata = envelope['metadata']
    assert data is None
    assert metadata['total_columns'] is None
    assert metadata['columns'] == []
    assert metadata['sample_bytes'] == 32768
    assert metadata['columns_truncated'] is True


@pytest.mark.parametrize('suffix,delimiter', [('.csv', ','), ('.tsv', '\t')])
def test_complete_multiline_header_is_counted_when_sample_continues(tmp_path, suffix, delimiter):
    root = tmp_path / 'workspace'
    root.mkdir()
    header = f'"first\nline"{delimiter}second\n'
    path = root / f'multiline{suffix}'
    path.write_bytes((header + f'1{delimiter}2\n' + 'x' * 400000).encode())

    _, envelope = regular_read(root, path.name)

    metadata = envelope['metadata']
    assert metadata['total_columns'] == 2
    assert metadata['columns'] == ['first\nline', 'second']
    assert metadata['columns_truncated'] is False


def test_empty_and_complete_single_line_headers_are_valid_and_preserve_bytes(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    empty = root / 'empty.csv'
    empty.write_bytes(b'')
    single = root / 'single.csv'
    single_bytes = b'name,value'
    single.write_bytes(single_bytes)

    empty_data, empty_envelope = regular_read(root, empty.name)
    single_data, single_envelope = regular_read(root, single.name)

    assert empty_data == b''
    assert empty_envelope['metadata']['total_columns'] == 0
    assert empty_envelope['metadata']['columns_truncated'] is False
    assert single_data == single_bytes
    assert single_envelope['metadata']['total_columns'] == 2
    assert single_envelope['metadata']['columns'] == ['name', 'value']
    assert single_envelope['metadata']['columns_truncated'] is False


@pytest.mark.parametrize('suffix', ['.csv', '.tsv'])
@pytest.mark.parametrize('size', [1, 262145])
def test_undecodable_bytes_keep_header_total_unknown_for_complete_and_sampled_files(tmp_path, suffix, size):
    root = tmp_path / 'workspace'
    root.mkdir()
    path = root / f'undecodable{suffix}'
    payload = b'\xff' * size
    path.write_bytes(payload)

    data, envelope = regular_read(root, path.name)

    metadata = envelope['metadata']
    if size == 1:
        assert data == payload
    else:
        assert data is None
    assert metadata['total_columns'] is None
    assert metadata['columns'] == []
    assert metadata['columns_truncated'] is True


@pytest.mark.parametrize('suffix,delimiter', [('.csv', ','), ('.tsv', '\t')])
@pytest.mark.parametrize('size', [64, 262145])
def test_invalid_utf8_in_header_is_unknown_without_losing_source_bytes(tmp_path, suffix, delimiter, size):
    root = tmp_path / 'workspace'
    root.mkdir()
    delimiter_bytes = delimiter.encode()
    header = b'name' + b'\xff' + delimiter_bytes + b'value\n'
    payload = header + b'1' + delimiter_bytes + b'2\n' + b'x' * max(0, size - len(header) - 4)
    path = root / f'invalid-header{suffix}'
    path.write_bytes(payload)

    data, envelope = regular_read(root, path.name)

    metadata = envelope['metadata']
    if len(payload) <= 262144:
        assert data == payload
    else:
        assert data is None
    assert metadata['total_columns'] is None
    assert metadata['columns'] == []
    assert metadata['columns_truncated'] is True


@pytest.mark.parametrize('suffix,delimiter', [('.csv', ','), ('.tsv', '\t')])
def test_valid_header_survives_invalid_body_and_partial_utf8_sample(tmp_path, suffix, delimiter):
    root = tmp_path / 'workspace'
    root.mkdir()
    delimiter_bytes = delimiter.encode()
    header = b'name' + delimiter_bytes + b'value\n'
    row = b'body' + b'\xff' + delimiter_bytes + b'row\n'
    filler = b'x' * (32768 - len(header) - len(row) - 1)
    payload = header + row + filler + b'\xc3\xa9' + b'x' * (262145 - 32768)
    path = root / f'valid-header{suffix}'
    path.write_bytes(payload)

    _, envelope = regular_read(root, path.name)

    metadata = envelope['metadata']
    assert metadata['total_columns'] == 2
    assert metadata['columns'] == ['name', 'value']
    assert metadata['sample_rows'][0] == ['body', 'row']
    assert metadata['columns_truncated'] is False
    assert len(json.dumps(metadata, ensure_ascii=False).encode()) <= 8192


@pytest.mark.parametrize('suffix,delimiter', [('.csv', ','), ('.tsv', '\t')])
def test_invalid_body_after_quoted_field_preserves_row_sampling(tmp_path, suffix, delimiter):
    root = tmp_path / 'workspace'
    root.mkdir()
    delimiter_bytes = delimiter.encode()
    payload = b'name' + delimiter_bytes + b'value\n"body"\xff' + delimiter_bytes + b'row\n'
    path = root / f'quoted-body{suffix}'
    path.write_bytes(payload)

    _, envelope = regular_read(root, path.name)

    metadata = envelope['metadata']
    assert metadata['columns'] == ['name', 'value']
    assert metadata['total_columns'] == 2
    assert metadata['sample_rows'] == [['body', 'row']]
    assert metadata['columns_truncated'] is False


def test_valid_replacement_character_in_header_is_preserved(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    payload = 'name�,value\n1,2\n'.encode()
    path = root / 'unicode-header.csv'
    path.write_bytes(payload)

    data, envelope = regular_read(root, path.name)

    assert data == payload
    metadata = envelope['metadata']
    assert metadata['columns'] == ['name�', 'value']
    assert metadata['total_columns'] == 2
    assert metadata['columns_truncated'] is False


@pytest.mark.parametrize('suffix,delimiter', [('.csv', ','), ('.tsv', '\t')])
def test_metadata_json_pruning_marks_column_omission(tmp_path, suffix, delimiter):
    root = tmp_path / 'workspace'
    root.mkdir()
    columns = [('column_' + str(index) + '_' + ('x' * 250)) for index in range(100)]
    path = root / f'wide{suffix}'
    path.write_bytes((delimiter.join(columns) + '\n' + delimiter.join('value' for _ in columns) + '\n').encode())
    # Keep this file metadata-only while retaining a complete header in the sample.
    with path.open('ab') as stream:
        stream.write(b'\n' * (262144 + 1))

    _, envelope = regular_read(root, path.name)

    metadata = envelope['metadata']
    assert metadata['total_columns'] == 100
    assert metadata['metadata_fields_truncated'] is True
    assert metadata['columns_truncated'] is True
    assert len(metadata['columns']) < 100
    assert all(len(column) <= 240 for column in metadata['columns'])
    assert len(json.dumps(metadata, ensure_ascii=False).encode()) <= 8192


@pytest.mark.parametrize('invalid_header', [False, True])
def test_source_endpoint_returns_delimited_metadata_without_starting_lifespan(invalid_header):
    """Exercise the actual source route against a synthetic project."""
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from services.api.common import project_dir
    from services.api.db import Branch, Session, migrate
    from services.api.main import app
    from services.observation.files import reconcile, sync_scopes
    from services.observation.models import ObservationScope
    from services.observation.reporting import ensure_state

    migrate()
    client = TestClient(app)
    try:
        response = client.post('/api/projects', json={'name': 'Metadata source fixture'})
        assert response.status_code == 200, response.text
        project_id = response.json()['id']
        root = project_dir(project_id)
        with Session.begin() as session:
            ensure_state(session, project_id)
            sync_scopes(session, project_id)
            branch = session.scalar(select(Branch).where(Branch.project_id == project_id))
            workspace = root / branch.workspace
            scope = session.scalar(select(ObservationScope).where(
                ObservationScope.project_id == project_id,
                ObservationScope.kind == 'branch_workspace',
                ObservationScope.object_id == branch.id,
            ))
            scope_id = scope.id
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / 'columns.csv'
        if invalid_header:
            path.write_bytes(b'name\xff,value\n1,2\n' + b'x' * 300000)
        else:
            columns = [f'field_{index:03d}' for index in range(101)]
            path.write_bytes((','.join(columns) + '\n' + ('value,' * 100) + 'value\n' + 'x' * 300000).encode())
        reconcile(scope_id)

        listing = client.get(f'/api/projects/{project_id}/progress/sources', params={'scope_id': scope_id})
        assert listing.status_code == 200, listing.text
        source = next(item['source'] for item in listing.json()['items'] if item['path'] == 'columns.csv')
        resolved = client.post(f'/api/projects/{project_id}/progress/source', json=source)
        assert resolved.status_code == 200, resolved.text
        resolved_body = resolved.json()
        assert resolved_body['availability'] == 'unavailable'
        metadata = resolved_body['metadata']
        if invalid_header:
            assert metadata['total_columns'] is None
            assert metadata['columns'] == []
            assert metadata['columns_truncated'] is True
        else:
            assert metadata['total_columns'] == 101
            assert metadata['columns_truncated'] is True
    finally:
        client.close()
