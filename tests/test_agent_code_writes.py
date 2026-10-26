"""Serial-path contracts of research.agents.code_writes.write_file_chunk.

Covers chunk validation, append addressing, replay skip and torn-tail repair,
and the absence of per-chunk prefix recopying (issue #413). All checks run
against real files in a temporary directory; no provider or model output is
substituted.
"""
import os
from pathlib import Path

import pytest

from research.agents.code_writes import CODE_CHUNK_CHARS, write_file_chunk


def test_serial_chunks_reassemble_exactly(tmp_path):
    path = tmp_path / 'module.py'
    first, second = 'def a():\n    return 1\n', 'def b():\n    return 2\n'
    res = write_file_chunk(path, first, 0)
    assert res == {'bytes_written': len(first.encode()), 'next_offset': len(first),
                   'size_bytes': len(first), 'replayed': False, 'exit_code': 0}
    res = write_file_chunk(path, second, len(first))
    assert res['replayed'] is False and res['size_bytes'] == len(first) + len(second)
    assert path.read_text() == first + second
    assert [p.name for p in tmp_path.iterdir()] == ['module.py']


def test_chunk_writes_create_missing_parents(tmp_path):
    path = tmp_path / 'pkg' / 'sub' / 'module.py'
    write_file_chunk(path, 'print(1)', 0)
    assert path.read_text() == 'print(1)'


def test_existing_file_mode_is_preserved(tmp_path):
    path = tmp_path / 'notes.txt'
    path.write_text('base')
    os.chmod(path, 0o600)
    write_file_chunk(path, ' more', 4)
    assert path.read_text() == 'base more'
    assert path.stat().st_mode & 0o777 == 0o600


def test_multibyte_content_round_trips_byte_exact(tmp_path):
    path = tmp_path / 'zh.txt'
    body = '研' * 1000
    write_file_chunk(path, body, 0)
    assert path.read_bytes() == body.encode('utf-8')


def test_offset_mismatch_is_rejected(tmp_path):
    path = tmp_path / 'module.py'
    write_file_chunk(path, 'abc', 0)
    with pytest.raises(ValueError, match='does not match current file size'):
        write_file_chunk(path, 'def', 1)


def test_chunk_content_validation(tmp_path):
    path = tmp_path / 'module.py'
    with pytest.raises(ValueError, match='Each chunk must contain'):
        write_file_chunk(path, '', 0)
    with pytest.raises(ValueError, match='Each chunk must contain'):
        write_file_chunk(path, 'a' * (CODE_CHUNK_CHARS + 1), 0)
    with pytest.raises(ValueError, match='nonnegative byte offset'):
        write_file_chunk(path, 'a', -1)
    with pytest.raises(ValueError, match='nonnegative byte offset'):
        write_file_chunk(path, 'a', True)


def test_replay_skips_already_durable_chunk(tmp_path):
    path = tmp_path / 'module.py'
    write_file_chunk(path, 'abcdef', 0)
    res = write_file_chunk(path, 'abcdef', 0, replay=True)
    assert res['replayed'] is True and res['size_bytes'] == 6
    assert path.read_text() == 'abcdef'


def test_replay_repairs_clean_torn_tail(tmp_path):
    path = tmp_path / 'module.py'
    write_file_chunk(path, 'prefix-', 0)
    torn = 'final-chunk-content'
    # A crash between the chunk write and its fsync can leave a truncated tail.
    with path.open('ab') as handle:
        handle.write(torn[:7].encode())
    assert path.read_text() == 'prefix-' + torn[:7]
    res = write_file_chunk(path, torn, 7, replay=True)
    assert res['replayed'] is False
    assert res['size_bytes'] == 7 + len(torn)
    assert path.read_text() == 'prefix-' + torn


def test_replay_refuses_mismatched_tail(tmp_path):
    path = tmp_path / 'module.py'
    write_file_chunk(path, 'prefix-', 0)
    with path.open('ab') as handle:
        handle.write(b'XXXXXXX')
    with pytest.raises(ValueError, match='does not match the durable tail'):
        write_file_chunk(path, 'final-chunk-content', 7, replay=True)
    assert path.read_text() == 'prefix-XXXXXXX'


def test_torn_tail_without_replay_is_rejected(tmp_path):
    path = tmp_path / 'module.py'
    write_file_chunk(path, 'prefix-', 0)
    with path.open('ab') as handle:
        handle.write(b'torn')
    with pytest.raises(ValueError, match='does not match current file size'):
        write_file_chunk(path, 'final-chunk-content', 7)
    assert path.read_text() == 'prefix-torn'


def test_owner_edit_between_calls_is_detected(tmp_path):
    path = tmp_path / 'module.py'
    write_file_chunk(path, 'base', 0)
    with path.open('ab') as handle:
        handle.write(b'-owner')
    with pytest.raises(ValueError, match='does not match current file size'):
        write_file_chunk(path, ' chunk', 4)
    assert path.read_text() == 'base-owner'


def test_no_prefix_recopy_on_large_output(tmp_path, monkeypatch):
    # The serial path must not read the accumulated prefix back per chunk:
    # instrument open() so any read-mode reopen of the growing target fails.
    path = tmp_path / 'big.bin'
    real_open = Path.open
    chunk = 'a' * 4000
    written = {'bytes': 0}
    total = 4000 * 40

    def guarded_open(self, *args, **kwargs):
        if args and args[0] == 'rb' and self == path and written['bytes'] >= 4000:
            raise AssertionError('chunk writer reopened the target for reading')
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded_open)
    offset = 0
    while offset < total:
        res = write_file_chunk(path, chunk, offset)
        offset = res['next_offset']
        written['bytes'] = offset
    assert path.stat().st_size == total
    assert path.read_text() == 'a' * total
