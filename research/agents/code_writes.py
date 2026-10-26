"""Incremental editable source writes with exact-offset crash replay.

Chunks are appended in place and fsynced, so total file I/O grows linearly
with the output size instead of recopying the accumulated prefix per chunk.
The file therefore contains a torn final chunk only if the process dies
between the chunk write and its fsync; a replayed call repairs a clean torn
tail (the stored bytes are a prefix of the chunk) by truncating back to the
chunk offset, and refuses to touch a tail that does not match.
"""
from __future__ import annotations

import os
from pathlib import Path

# This bounds one model tool payload, never the number of chunks or file size.
CODE_CHUNK_CHARS = 4000


def _identity(stat):
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_mode) if stat else None


def write_file_chunk(path, content, offset, *, replay=False):
    path = Path(path)
    if not isinstance(content, str) or not 0 < len(content) <= CODE_CHUNK_CHARS:
        raise ValueError(f'Each chunk must contain 1–{CODE_CHUNK_CHARS} characters; continue with another chunk')
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError('offset must be a nonnegative byte offset')
    data = content.encode('utf-8')
    before = path.stat() if path.exists() else None
    size = before.st_size if before else 0
    if replay and size >= offset + len(data):
        with path.open('rb') as existing:
            existing.seek(offset)
            if existing.read(len(data)) == data:
                return {'bytes_written': len(data), 'next_offset': offset + len(data),
                        'size_bytes': size, 'replayed': True, 'exit_code': 0}
    if replay and offset < size < offset + len(data):
        with path.open('rb') as existing:
            existing.seek(offset)
            if existing.read(size - offset) != data[:size - offset]:
                raise ValueError('Replayed chunk does not match the durable tail; read the file before retrying')
        os.truncate(path, offset)
        before = path.stat()
        size = before.st_size
    if size != offset:
        raise ValueError(f'Chunk offset {offset} does not match current file size {size}; read the file before choosing the next offset')
    path.parent.mkdir(parents=True, exist_ok=True)
    if before is None:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o666)
            created = True
        except FileExistsError:
            raise ValueError('File appeared while preparing this chunk; read it before retrying') from None
    else:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        created = False
    try:
        # Fstat the same descriptor the chunk is written through: an owner edit
        # between the call-start stat and this open shows up as a new identity,
        # while our own append is not yet visible.
        if not created and _identity(os.fstat(fd)) != _identity(before):
            raise ValueError('File changed while preparing this chunk; read it before retrying')
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    if created:
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return {'bytes_written': len(data), 'next_offset': offset + len(data),
            'size_bytes': offset + len(data), 'replayed': False, 'exit_code': 0}
