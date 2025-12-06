"""File utilities: preallocation, chunk writing, and SHA256 helpers.

Exports:
- preallocate_file(path, size)
- write_chunk(path, offset, data)
- get_chunk_sha256(data: bytes) -> str
- sha256_file(path) -> str

Note: `transfer.py` passes raw bytes to `get_chunk_sha256`, so this
implementation provides a bytes-based helper.
"""
import os
import hashlib
from typing import Union


BUF_SIZE = 64 * 1024


def preallocate_file(path: str, size: int) -> None:
    """Create or truncate a file to `size` bytes. Creates parent directories.

    Uses truncate which creates a sparse file on many filesystems. This is
    sufficient for the receiver which will write chunks at offsets later.
    """
    dirpath = os.path.dirname(path) or '.'
    os.makedirs(dirpath, exist_ok=True)
    with open(path, 'wb') as f:
        f.truncate(size)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def write_chunk(path: str, offset: int, data: bytes) -> None:
    """Write bytes to `path` at `offset`.

    Uses seek + write for portability. Flushes and fsyncs to reduce data loss
    risk on crashes.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, 'r+b') as f:
        f.seek(offset)
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def get_chunk_sha256(data: Union[bytes, bytearray]) -> str:
    """Return SHA256 hex digest for the provided bytes-like object.

    The `transfer` implementation sends in-memory chunk bytes to the
    receiver, which calls this helper with the received bytes, so the
    convenient API is bytes-in -> hex-out.
    """
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: str) -> str:
    """Compute SHA256 for the whole file at `path` streaming in BUF_SIZE.
    Returns the hex digest.
    """
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
