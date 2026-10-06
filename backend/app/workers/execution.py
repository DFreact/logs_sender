from uuid import UUID, uuid4

from app.api.errors import log_failure
from app.services.normalization import normalize
from app.storage.files import StorageError
from app.storage.service import collect, delete_effect
from app.workers.queue import LostLease, claim, complete, fail


def execute(factory, store, settings, identifier: str, *, transactional_handlers=None):
    try:
        item_id = UUID(identifier)
    except (ValueError, TypeError, AttributeError):
        return
    from app.persistence.models import WorkItem
    from app.services.notifications.delivery import execute as deliver

    with factory() as db:
        item = db.get(WorkItem, item_id)
        delivery = item is not None and item.kind == "DELIVER"
    if delivery:
        try:
            deliver(factory, settings, item_id)
        except Exception as error:
            # A committed attempt is recovered from its own lease; never blindly resend.
            log_failure(error, uuid4().hex)
        return
    lease = None
    try:
        with factory.begin() as db:
            lease = claim(db, item_id, settings.lease_seconds)
        if lease is None:
            return
        if lease.kind == "NORMALIZE":
            normalize(factory, store, settings, lease)
        elif lease.kind == "BLOB_DELETE":
            delete_effect(factory, store, lease)
        elif lease.kind == "RETENTION":
            from app.services.retention.worker import execute as retain

            retain(factory, settings, lease)
        elif lease.kind == "STORAGE_COLLECT":
            collect(factory, store, settings)
            from app.observability.metrics import sample

            sample(factory, settings, lease.team_id)
            with factory.begin() as db:
                complete(db, lease)
        elif transactional_handlers and lease.kind in transactional_handlers:
            with factory.begin() as db:
                complete(
                    db, lease, lambda session: transactional_handlers[lease.kind](session, lease)
                )
        else:
            with factory.begin() as db:
                fail(db, lease, "UNKNOWN_TASK", permanent=True)
    except LostLease:
        return
    except Exception as error:
        log_failure(error, uuid4().hex)
        if lease:
            try:
                with factory.begin() as db:
                    fail(
                        db,
                        lease,
                        "BLOB_INVALID" if isinstance(error, StorageError) else "TASK_FAILED",
                    )
            except Exception as failure:
                # DB outage: the lease itself will expire and scheduler recovers it.
                log_failure(failure, uuid4().hex)
