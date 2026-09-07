from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4


class ManagedFiles:
    """Cogent-owned files are addressed relative to a trusted root, never a model path."""

    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()

    @contextmanager
    def parent(self, relative: str, *, create=False):
        parts = PurePosixPath(relative).parts
        if not parts or PurePosixPath(relative).is_absolute() or any(p in {'..', '.', ''} for p in parts):
            raise ValueError('Invalid managed file path')
        descriptors = []
        try:
            fd = os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(fd)
            for part in [*self.root.parts[1:], *parts[:-1]]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            yield fd, parts[-1]
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def read(self, relative: str, *, limit: int | None = 8_000_000) -> bytes | None:
        try:
            with self.parent(relative) as (parent, name):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, 'rb') as stream:
                    import stat
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError('Managed file must be a regular file')
                    data = stream.read() if limit is None else stream.read(limit + 1)
                    if limit is not None and len(data) > limit:
                        raise ValueError('Managed file exceeds its size limit')
                    return data
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError('Managed file path contains a symbolic link') from exc
            raise

    def exists(self, relative: str) -> bool:
        try:
            with self.parent(relative) as (parent, name):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    import stat
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ValueError('Managed file must be a regular file')
                finally:
                    os.close(fd)
                return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError('Managed file path contains a symbolic link') from exc
            raise

    def write(self, relative: str, data: bytes):
        with self.parent(relative, create=True) as (parent, name):
            temp = f'.cogent-write-{uuid4().hex}'
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temp, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def delete(self, relative: str) -> None:
        with self.parent(relative) as (parent, name):
            try:
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            except FileNotFoundError:
                return

    @contextmanager
    def lock(self, relative: str):
        import fcntl
        with self.parent(relative, create=True) as (parent, name):
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
