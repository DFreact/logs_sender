import hashlib
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.persistence.models import BlobRecord, StorageScan, WorkItem
from app.storage.files import BlobStore, StorageError
from app.storage.service import attach, collect, request_delete, stage
from app.workers.execution import execute
from app.workers.queue import clock


def cleanup_tasks(background):
    factory, store, settings, _ = background
    with factory() as db:
        identifiers = db.scalars(select(WorkItem.id).where(WorkItem.kind == "BLOB_DELETE")).all()
    for identifier in identifiers:
        execute(factory, store, settings, str(identifier))


def age_record(factory, identifier):
    with factory.begin() as db:
        db.get(BlobRecord, identifier).created_at = clock(db) - timedelta(days=2)


def test_immutable_write_checksum_and_size_limit(background):
    factory, store, _, _ = background
    identifier = stage(factory, store, [b"original ", b"message"])
    with factory.begin() as db:
        record = db.get(BlobRecord, identifier)
        assert (
            record.size == 16 and record.checksum == hashlib.sha256(b"original message").hexdigest()
        )
        assert store.read(identifier, record.size, record.checksum) == b"original message"
        attach(db, identifier, uuid4(), "TEST")
    with pytest.raises(StorageError, match="BLOB_ALREADY_EXISTS"):
        store.write(identifier, [b"replacement"])
    store._path(identifier).write_bytes(b"corrupt")
    with pytest.raises(StorageError, match="BLOB_CHECKSUM_MISMATCH"):
        store.read(identifier, 16, record.checksum)
    store.max_bytes = 4
    oversize = uuid4()
    with pytest.raises(StorageError, match="BLOB_TOO_LARGE"):
        store.write(oversize, [b"123", b"45"])
    assert not store._path(oversize).exists()
    assert not store._path(oversize).with_suffix(".tmp").exists()


def test_paths_and_symlinks_cannot_escape_storage(background, tmp_path):
    _, store, _, _ = background
    with pytest.raises(StorageError, match="INVALID_BLOB_ID"):
        store.write("../outside", [b"bad"])
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    identifier = uuid4()
    store._path(identifier).symlink_to(outside)
    with pytest.raises(StorageError, match="BLOB_ALREADY_EXISTS"):
        store.write(identifier, [b"bad"])
    with pytest.raises(OSError):
        store.read(identifier, 7, hashlib.sha256(b"private").hexdigest())
    store.delete(identifier)
    assert outside.read_bytes() == b"private"
    link_root = tmp_path / "root-link"
    link_root.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(StorageError, match="INVALID_STORAGE_DIRECTORY"):
        BlobStore(link_root, 100).initialize()


@pytest.mark.parametrize("failure_point", ["file", "directory"])
def test_failed_fsync_never_attaches_or_confirms_a_file(background, monkeypatch, failure_point):
    factory, store, _, _ = background

    def failure(_):
        raise OSError("disk secret")

    if failure_point == "file":
        monkeypatch.setattr(os, "fsync", failure)
    else:
        monkeypatch.setattr(store, "_sync", failure)
    with pytest.raises(OSError):
        stage(factory, store, [b"original"])
    with factory.begin() as db:
        record = db.scalar(select(BlobRecord))
        assert record.state == "STAGING" and record.checksum is None
        with pytest.raises(StorageError, match="BLOB_NOT_READY"):
            attach(db, record.id, uuid4(), "TEST")
    assert not list(store.root.glob("*/*.tmp"))


def test_business_rollback_leaves_recoverable_staging_and_ready_is_protected(background):
    factory, store, settings, _ = background
    orphan = stage(factory, store, [b"orphan"])
    ready = stage(factory, store, [b"referenced"])
    with pytest.raises(RuntimeError):
        with factory.begin() as db:
            attach(db, orphan, uuid4(), "TEST")
            raise RuntimeError("rollback")
    with factory.begin() as db:
        attach(db, ready, uuid4(), "TEST")
    age_record(factory, orphan)
    age_record(factory, ready)
    collect(factory, store, settings)
    cleanup_tasks(background)
    with factory() as db:
        assert db.get(BlobRecord, orphan).state == "DELETED"
        assert db.get(BlobRecord, ready).state == "READY"
    assert not store._path(orphan).exists() and store._path(ready).exists()


def test_grace_period_and_late_attachment_are_safe(background):
    factory, store, settings, _ = background
    identifier = stage(factory, store, [b"active writer"])
    collect(factory, store, settings)
    with factory() as db:
        assert db.get(BlobRecord, identifier).state == "STAGING"
    age_record(factory, identifier)
    collect(factory, store, settings)
    with factory.begin() as db:
        with pytest.raises(StorageError, match="BLOB_NOT_READY"):
            attach(db, identifier, uuid4(), "TEST")
    cleanup_tasks(background)
    assert not store._path(identifier).exists()


@pytest.mark.parametrize("temporary", [False, True])
def test_file_without_database_record_is_eventually_deleted(background, temporary):
    factory, store, settings, _ = background
    identifier = uuid4()
    store.write(identifier, [b"orphan"])
    path = store._path(identifier)
    if temporary:
        destination = path.with_suffix(".tmp")
        path.rename(destination)
        path = destination
    os.utime(path, (1, 1))
    with factory.begin() as db:
        scan = db.get(StorageScan, 1)
        scan.shard = int(identifier.hex[:2], 16)
    collect(factory, store, settings)
    cleanup_tasks(background)
    assert not path.exists()
    with factory() as db:
        assert db.get(BlobRecord, identifier).state == "DELETED"


def test_delete_recovers_after_file_removed_before_database_commit(background, monkeypatch):
    factory, store, settings, _ = background
    identifier = stage(factory, store, [b"delete me"])
    with factory.begin() as db:
        record = db.get(BlobRecord, identifier)
        request_delete(db, record)
        work_id = db.scalar(select(WorkItem.id).where(WorkItem.entity_id == identifier))
    original = store.delete

    def crash_after_delete(identifier):
        original(identifier)
        raise OSError("simulate crash boundary")

    monkeypatch.setattr(store, "delete", crash_after_delete)
    execute(factory, store, settings, str(work_id))
    with factory.begin() as db:
        assert db.get(BlobRecord, identifier).state == "DELETING"
        db.get(WorkItem, work_id).due_at = clock(db)
    monkeypatch.setattr(store, "delete", original)
    execute(factory, store, settings, str(work_id))
    with factory() as db:
        assert db.get(BlobRecord, identifier).state == "DELETED"
        assert db.get(WorkItem, work_id).state == "SUCCEEDED"


def test_late_writer_after_tombstone_is_reaped(background):
    factory, store, settings, _ = background
    identifier = stage(factory, store, [b"first"])
    with factory.begin() as db:
        request_delete(db, db.get(BlobRecord, identifier))
    cleanup_tasks(background)
    store.write(identifier, [b"late writer"])
    os.utime(store._path(identifier), (1, 1))
    with factory.begin() as db:
        db.get(StorageScan, 1).shard = int(identifier.hex[:2], 16)
    collect(factory, store, settings)
    cleanup_tasks(background)
    assert not store._path(identifier).exists()
