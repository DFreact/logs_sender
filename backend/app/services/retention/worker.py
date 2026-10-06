from time import monotonic

from sqlalchemy import select

from app.persistence.models import Team
from app.services.retention.cleanup import run_batch
from app.workers.queue import LostLease, complete, enqueue, renew


def execute(factory, settings, lease):
    deadline = monotonic() + 5
    changed = 0
    for _ in range(20):
        with factory.begin() as db:
            if not renew(db, lease, settings.lease_seconds):
                raise LostLease()
        changed = run_batch(factory, settings, lease.team_id)
        if not changed or monotonic() >= deadline:
            break
    with factory.begin() as db:
        # Serialize continuation with scheduler's active-chain check.
        db.scalar(select(Team).where(Team.id == lease.team_id).with_for_update())

        def continuation(session):
            if changed:
                enqueue(
                    session,
                    "RETENTION",
                    lease.team_id,
                    f"retention-next:{lease.id}",
                    team_id=lease.team_id,
                )

        complete(db, lease, continuation)
