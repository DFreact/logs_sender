"""Local immutable objects. No user supplied filenames become filesystem paths."""

import fcntl
import hashlib
import heapq
import os
import re
import shutil
import stat
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4


class StorageError(Exception):
    pass


class BlobStore:
    def __init__(self, root: Path, max_bytes: int, min_free_bytes: int = 0):
        self.root, self.max_bytes, self.min_free_bytes = root, max_bytes, min_free_bytes

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._directory(self.root)
        for name in ["locks", *[f"{number:02x}" for number in range(256)]]:
            path = self.root / name
            path.mkdir(exist_ok=True, mode=0o700)
            self._directory(path)
        self._sync(self.root)

    @staticmethod
    def _directory(path: Path):
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise StorageError("INVALID_STORAGE_DIRECTORY")

    @staticmethod
    def _sync(path: Path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _path(self, identifier: UUID) -> Path:
        if not isinstance(identifier, UUID):
            raise StorageError("INVALID_BLOB_ID")
        parent = self.root / identifier.hex[:2]
        self._directory(self.root)
        self._directory(parent)
        return parent / identifier.hex

    @contextmanager
    def locked(self, identifier: UUID):
        self._directory(self.root / "locks")
        # Fixed striped locks never get unlinked; inode replacement cannot split a lock.
        fd = os.open(
            self.root / "locks" / identifier.hex[:2], os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def write(self, identifier: UUID, chunks: Iterable[bytes]) -> tuple[int, str]:
        path = self._path(identifier)
        temporary = path.with_suffix(".tmp")
        with self.locked(identifier):
            if path.exists() or path.is_symlink():
                raise StorageError("BLOB_ALREADY_EXISTS")
            if shutil.disk_usage(self.root).free < self.min_free_bytes:
                raise StorageError("STORAGE_LOW_SPACE")
            checksum, size = hashlib.sha256(), 0
            try:
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as output:
                    for chunk in chunks:
                        if not isinstance(chunk, bytes):
                            raise StorageError("INVALID_BLOB_CHUNK")
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise StorageError("BLOB_TOO_LARGE")
                        checksum.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
                self._sync(path.parent)
                return size, checksum.hexdigest()
            except BaseException:
                temporary.unlink(missing_ok=True)
                # A final object after a failed directory fsync remains unconfirmed;
                # its STAGING record/orphan scan schedules deletion later.
                raise

    def read(self, identifier: UUID, size: int, checksum: str) -> bytes:
        path = self._path(identifier)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise StorageError("INVALID_BLOB_FILE")
            data = source.read(self.max_bytes + 1)
        if (
            len(data) != size
            or len(data) > self.max_bytes
            or hashlib.sha256(data).hexdigest() != checksum
        ):
            raise StorageError("BLOB_CHECKSUM_MISMATCH")
        return data

    def delete(self, identifier: UUID):
        path = self._path(identifier)
        with self.locked(identifier):
            path.unlink(missing_ok=True)
            path.with_suffix(".tmp").unlink(missing_ok=True)
            self._sync(path.parent)

    def probe(self) -> str:
        self._directory(self.root)
        if shutil.disk_usage(self.root).free < self.min_free_bytes:
            return "LOW_SPACE"
        identifier = uuid4()
        self.write(identifier, [b"eventhub-storage-probe"])
        self.delete(identifier)
        return "OK"

    def scan(self, shard: int, cursor: str, limit: int) -> list[tuple[str, UUID, float]]:
        directory = self.root / f"{shard:02x}"
        self._directory(directory)

        def candidates(entries):
            for entry in entries:
                if (
                    entry.name <= cursor
                    or not entry.name.startswith(f"{shard:02x}")
                    or not re.fullmatch(r"[a-f0-9]{32}(?:\.tmp)?", entry.name)
                ):
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue  # A concurrent deletion or completed probe is harmless.
                if stat.S_ISREG(metadata.st_mode):
                    yield entry.name, metadata.st_mtime

        with os.scandir(directory) as entries:
            # Memory is bounded by batch size; one shard is traversed per batch.
            selected = heapq.nsmallest(limit, candidates(entries))
        return [(name, UUID(hex=name[:32]), mtime) for name, mtime in selected]
