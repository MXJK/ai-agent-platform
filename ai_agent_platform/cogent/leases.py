"""Exclusive Run execution ownership, released by the OS on process exit."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path


class RunLeaseUnavailable(RuntimeError):
    pass


@contextmanager
def file_run_lease(root: Path, run_id: str):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = hashlib.sha256(run_id.encode()).hexdigest() + '.lock'
    fd = os.open(root / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunLeaseUnavailable('Run is owned by another worker') from exc
        yield
    finally:
        os.close(fd)
