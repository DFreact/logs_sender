"""Streaming, regular-file-only storage transfer; executed inside the backend image."""

import hashlib
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath


def valid(name, directory=False):
    pattern = (
        r"(?:[a-f0-9]{2}|locks)"
        if directory
        else r"(?:[a-f0-9]{2}/[a-f0-9]{32}(?:\.tmp)?|locks/[a-f0-9]{2})"
    )
    matched = bool(re.fullmatch(pattern, name))
    return matched and (directory or name.startswith("locks/") or name[:2] == name[3:5])


def transfer(action, root):
    root = Path(root)
    if action == "save":
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            for parent in sorted(root.iterdir()):
                if parent.is_symlink() or not parent.is_dir() or not valid(parent.name, True):
                    raise ValueError("INVALID_STORAGE")
                archive.add(parent, arcname=parent.name, recursive=False)
                archive.members.clear()
                with os.scandir(parent) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        name = f"{parent.name}/{path.name}"
                        if path.is_symlink() or not path.is_file() or not valid(name):
                            raise ValueError("INVALID_STORAGE")
                        archive.add(path, arcname=name, recursive=False)
                        archive.members.clear()
    elif action == "restore":
        if any(root.iterdir()):
            raise ValueError("STORAGE_NOT_EMPTY")
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
            for item in archive:
                if not (item.isdir() or item.isfile()) or not valid(item.name, item.isdir()):
                    raise ValueError("INVALID_ARCHIVE")
                path = root.joinpath(*PurePosixPath(item.name).parts)
                if item.isdir():
                    path.mkdir(mode=0o700, exist_ok=True)
                else:
                    path.parent.mkdir(mode=0o700, exist_ok=True)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as target, archive.extractfile(item) as source:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                        target.flush()
                        os.fsync(target.fileno())
                archive.members.clear()
        for directory in [*root.iterdir(), root]:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    elif action == "check":
        # Verify all retained files against durable metadata before starting writers.
        from app.persistence.database import build_engine, build_session_factory
        from app.persistence.models import BlobRecord, NotificationChannel
        from app.security.channel_secrets import reveal
        from app.settings import Settings
        from sqlalchemy import select

        engine = build_engine(Settings())
        checked = 0
        with build_session_factory(engine)() as db:
            for blob in db.scalars(
                select(BlobRecord)
                .where(BlobRecord.state == "READY")
                .execution_options(yield_per=100)
            ):
                path = root / blob.id.hex[:2] / blob.id.hex
                checksum = hashlib.sha256()
                size = 0
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        checksum.update(chunk)
                        size += len(chunk)
                if size != blob.size or checksum.hexdigest() != blob.checksum:
                    raise ValueError("BLOB_MISMATCH")
                checked += 1
            for channel in db.scalars(select(NotificationChannel).execution_options(yield_per=100)):
                reveal(Settings(), channel.team_id, channel.id, channel.secret_ciphertext)
        print(checked)


if __name__ == "__main__":
    transfer(sys.argv[1], "/var/lib/eventhub/blobs")
