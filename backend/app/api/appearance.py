from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import audit_request
from app.api.dependencies import Actor, Database, require
from app.api.errors import ApiFailure, ErrorCode
from app.persistence.models import DEFAULT_TEAM_ID, Team
from app.security.audit import AuditAction, record
from app.security.permissions import Permission

router = APIRouter(prefix="/api/v1/settings/appearance")


class Appearance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    palette: Literal["blue", "teal", "green", "violet", "wine", "slate"]
    mode: Literal["light", "dark"]
    version: Annotated[int, Field(strict=True, ge=1)]


def view(team):
    return Appearance(
        palette=team.appearance_palette, mode=team.appearance_mode, version=team.appearance_version
    )


@router.get("", response_model=Appearance)
def get_appearance(db: Database):
    # Public whitelist only: the sign-in page uses the same installation palette.
    team = db.get(Team, DEFAULT_TEAM_ID)
    if team is None:
        raise ApiFailure(503, ErrorCode.SERVICE_UNAVAILABLE)
    return view(team)


@router.patch("", response_model=Appearance)
def save_appearance(
    body: Appearance,
    request: Request,
    db: Database,
    actor: Annotated[Actor, Depends(require(Permission.MANAGE_CONFIGURATION))],
):
    # get_actor locks the team before checking authorization and CSRF.
    team = db.get(Team, actor.membership.team_id)
    if body.version != team.appearance_version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    before = {
        "appearance_palette": team.appearance_palette,
        "appearance_mode": team.appearance_mode,
    }
    after = {"appearance_palette": body.palette, "appearance_mode": body.mode}
    if before != after:
        team.appearance_palette = body.palette
        team.appearance_mode = body.mode
        team.appearance_version += 1
        record(
            db,
            AuditAction.APPEARANCE_UPDATED,
            actor_id=actor.user.id,
            entity_type="APPEARANCE",
            entity_id=team.id,
            before=before,
            after=after,
            **audit_request(request),
        )
        db.commit()
    return view(team)
