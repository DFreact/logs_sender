import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.persistence.models import User, WorkItem
from app.scheduler.service import schedule_once
from app.workers.dispatcher import dispatch_once
from app.workers.execution import execute
from app.workers.queue import (
    claim,
    clock,
    complete,
    enqueue,
    fail,
    recover_expired,
    release_publication,
    renew,
    reserve_publications,
)


def item(factory, **options):
    with factory.begin() as db:
        return enqueue(db, "TEST", uuid4(), uuid4().hex, **options)


def test_task_is_created_atomically_and_deduplicated(background):
    factory, _, _, user_id = background
    key, entity = uuid4().hex, uuid4()
    with pytest.raises(RuntimeError):
        with factory.begin() as db:
            db.get(User, user_id).version += 1
            enqueue(db, "TEST", entity, key)
            raise RuntimeError("rollback")
    with factory.begin() as db:
        assert db.get(User, user_id).version == 1
        assert not db.scalars(select(WorkItem)).all()
        first = enqueue(db, "TEST", entity, key)
        assert enqueue(db, "TEST", entity, key) == first
        with pytest.raises(ValueError, match="WORK_DEDUPE_CONFLICT"):
            enqueue(db, "TEST", uuid4(), key)


def test_due_time_and_duplicate_wakeups_apply_effect_once(background):
    factory, _, settings, user_id = background
    with factory() as db:
        now = clock(db)
    identifier = item(factory, due_at=now + timedelta(seconds=100))
    with factory.begin() as db:
        assert claim(db, identifier, settings.lease_seconds, now=now) is None
    with factory.begin() as db:
        lease = claim(db, identifier, 60, now=now + timedelta(seconds=101))
    for _ in range(3):
        with factory.begin() as db:
            changed = complete(
                db,
                lease,
                lambda session: setattr(session.get(User, user_id), "version", 2),
                now=now + timedelta(seconds=102),
            )
            assert changed is (_ == 0)
    with factory() as db:
        assert db.get(User, user_id).version == 2
        assert db.get(WorkItem, identifier).attempts == 1


def test_expired_owner_cannot_finish_fail_or_renew(background):
    factory, _, _, user_id = background
    identifier = item(factory)
    with factory.begin() as db:
        now = clock(db)
        old = claim(db, identifier, 10, now=now)
    later = now + timedelta(seconds=11)
    with factory.begin() as db:
        assert recover_expired(db, 10, now=later) == 1
        fresh = claim(db, identifier, 60, now=later)
    assert fresh.token != old.token
    with factory.begin() as db:
        assert not complete(db, old, lambda _: pytest.fail("stale effect executed"), now=later)
        assert not fail(db, old, "TASK_FAILED", now=later)
        assert not renew(db, old, 60, now=later)
        assert renew(db, fresh, 120, now=later)
        assert complete(db, fresh, now=later)


def test_failure_rolls_back_business_effect_and_retries_are_bounded(background):
    factory, store, settings, user_id = background
    identifier = item(factory, max_attempts=2)

    def broken(db, _lease):
        db.get(User, user_id).version += 1
        db.flush()
        raise RuntimeError("secret-token must not escape")

    execute(factory, store, settings, str(identifier), transactional_handlers={"TEST": broken})
    with factory.begin() as db:
        work = db.get(WorkItem, identifier)
        assert work.state == "PENDING" and work.failure_code == "TASK_FAILED"
        assert work.due_at is not None and db.get(User, user_id).version == 1
        work.due_at = clock(db)
    execute(factory, store, settings, str(identifier), transactional_handlers={"TEST": broken})
    with factory() as db:
        work = db.get(WorkItem, identifier)
        assert work.state == "FAILED" and work.attempts == 2
        assert db.get(User, user_id).version == 1


def test_exhausted_crashed_task_is_terminal(background):
    factory, _, _, _ = background
    identifier = item(factory, max_attempts=1)
    with factory.begin() as db:
        now = clock(db)
        claim(db, identifier, 10, now=now)
    with factory.begin() as db:
        recover_expired(db, 100, now=now + timedelta(seconds=11))
        assert db.get(WorkItem, identifier).state == "FAILED"
        assert claim(db, identifier, 10, now=now + timedelta(seconds=12)) is None


def test_publication_recovery_after_transport_loss_and_stale_release(background):
    factory, _, settings, _ = background
    identifier = item(factory)

    def unavailable(_):
        raise ConnectionError("redis password=must-not-be-used")

    with pytest.raises(ConnectionError):
        dispatch_once(factory, settings, unavailable)
    with factory.begin() as db:
        now = clock(db)
        assert db.get(WorkItem, identifier).publication_at is None
        first = reserve_publications(db, 10, 10, now=now)
    with factory.begin() as db:
        assert reserve_publications(db, 10, 10, now=now) == []
        second = reserve_publications(db, 10, 10, now=now + timedelta(seconds=11))
        release_publication(db, identifier, first[0][1])
        db.expire_all()
        assert db.get(WorkItem, identifier).publication_token == second[0][1]
        assert db.get(WorkItem, identifier).attempts == 0


def test_scheduler_restart_deduplicates_maintenance(background):
    factory, _, settings, _ = background
    settings.maintenance_seconds = 3600
    schedule_once(factory, settings)
    schedule_once(factory, settings)
    with factory() as db:
        assert (
            len(db.scalars(select(WorkItem).where(WorkItem.kind == "STORAGE_COLLECT")).all()) == 1
        )


def test_unknown_task_fails_without_exposing_details(background):
    factory, store, settings, _ = background
    identifier = item(factory)
    execute(factory, store, settings, str(identifier))
    execute(factory, store, settings, "not-a-uuid")
    with factory() as db:
        work = db.get(WorkItem, identifier)
        assert work.state == "FAILED" and work.failure_code == "UNKNOWN_TASK"


@pytest.mark.skipif(not os.environ.get("HUB_TEST_POSTGRES_PORT"), reason="PostgreSQL concurrency")
def test_concurrent_claim_and_effect_are_exclusive(background):
    factory, _, _, user_id = background
    identifier = item(factory)
    barrier = Barrier(8)

    def worker(_):
        barrier.wait()
        with factory.begin() as db:
            lease = claim(db, identifier, 60)
        if lease:
            with factory.begin() as db:

                def effect(session):
                    session.get(User, user_id).version += 1

                complete(db, lease, effect)
        return lease is not None

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(worker, range(8))) == 1
    with factory() as db:
        assert db.get(User, user_id).version == 2


@pytest.mark.skipif(not os.environ.get("HUB_TEST_POSTGRES_PORT"), reason="PostgreSQL concurrency")
def test_concurrent_dispatchers_reserve_disjoint_batches(background):
    factory, _, _, _ = background
    for _ in range(10):
        item(factory)
    barrier = Barrier(2)

    def reserve(_):
        barrier.wait()
        with factory.begin() as db:
            return {identifier for identifier, _ in reserve_publications(db, 60, 5)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(reserve, range(2))
    assert len(first | second) == 10 and not first & second


def test_lease_expiring_during_effect_rolls_back_the_effect(background, monkeypatch):
    from app.workers import queue

    factory, _, _, user_id = background
    identifier = item(factory)
    with factory.begin() as db:
        now = clock(db)
        lease = claim(db, identifier, 10, now=now)
    times = iter([now, now + timedelta(seconds=11)])
    monkeypatch.setattr(queue, "clock", lambda _: next(times))
    with pytest.raises(queue.LostLease):
        with factory.begin() as db:
            complete(db, lease, lambda session: setattr(session.get(User, user_id), "version", 2))
    with factory() as db:
        assert db.get(User, user_id).version == 1
        assert db.get(WorkItem, identifier).state == "RUNNING"
