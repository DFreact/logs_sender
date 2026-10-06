from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse, Response

from app.api.dependencies import Actor, Database, require
from app.observability.health import infrastructure, snapshot
from app.observability.metrics import current
from app.security.permissions import Permission

router = APIRouter()


def inspect_state(request: Request, db):
    # Release authentication's read transaction before filesystem/network probes.
    db.rollback()
    components = infrastructure(request.app.state.settings)
    return snapshot(db, request.app.state.settings, probe=lambda _: components)


@router.get("/api/v1/health")
def system_health(
    request: Request,
    db: Database,
    actor: Annotated[Actor, Depends(require(Permission.VIEW_SYSTEM_HEALTH))],
):
    return {**inspect_state(request, db), "metrics": current(db, actor.membership.team_id)}


@router.get("/health/ready")
def readiness(request: Request, db: Database):
    state = inspect_state(request, db)
    ready = all(component["status"] == "HEALTHY" for component in state["components"])
    # No component names, counts, settings or exception details without authentication.
    return JSONResponse(
        {"status": "HEALTHY" if ready else "DEGRADED"}, status_code=200 if ready else 503
    )


@router.get("/metrics")
def metrics(db: Database, actor: Annotated[Actor, Depends(require(Permission.VIEW_SYSTEM_HEALTH))]):
    data = current(db, actor.membership.team_id)
    lines = [f"eventhub_{key.lower()} {value}" for key, value in sorted(data["counters"].items())]
    lines.append(f"eventhub_sample_fresh {int(data['fresh'])}")
    for key, value in data["rates"].items():
        if value is not None:
            lines.append(f"eventhub_{key.removesuffix('_total')}_per_second {value}")
    if data["disk"]:
        lines.extend(f"eventhub_storage_{key}_bytes {value}" for key, value in data["disk"].items())
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/api/v1/overview")
def overview(db: Database, actor: Annotated[Actor, Depends(require(Permission.VIEW_EVENTS))]):
    from app.observability.metrics import counters

    data = counters(db, actor.membership.team_id)
    return {
        key: data.get(key, 0)
        for key in ("events", "events_new", "events_critical", "notification_FAILED")
    }
