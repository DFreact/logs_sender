import hmac
import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiFailure, ErrorCode
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import DEFAULT_TEAM_ID, AuthSession, Membership, Team, User, utcnow
from app.security.permissions import Permission, permitted
from app.security.tokens import TOKEN_RE, csrf_for_session, digest, origin_guard


def get_db(request: Request):
    with request.app.state.database_lock:
        if request.app.state.session_factory is None:
            engine = build_engine(request.app.state.settings)
            request.app.state.engine = engine
            request.app.state.session_factory = build_session_factory(engine)
    with request.app.state.session_factory() as db:
        yield db


Database = Annotated[Session, Depends(get_db)]


def lock_team(db: Session):
    db.execute(select(Team).where(Team.id == DEFAULT_TEAM_ID).with_for_update()).scalar_one()


@dataclass
class Actor:
    user: User
    membership: Membership
    session: AuthSession
    token: str


def get_actor(request: Request, db: Database) -> Actor:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie, "")
    if not TOKEN_RE.fullmatch(token):
        raise ApiFailure(401, ErrorCode.SESSION_EXPIRED)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin_guard(request, settings)
        expected = csrf_for_session(settings, token)
        supplied = request.headers.get("x-csrf-token", "")
        if not re.fullmatch(r"[a-f0-9]{64}", supplied) or not hmac.compare_digest(
            expected, supplied
        ):
            raise ApiFailure(403, ErrorCode.CSRF_FAILED)
        # All account mutations (including revocation) use this lock BEFORE reading
        # authorization. A demoted/disabled administrator cannot act after waiting.
        lock_team(db)
    row = db.execute(
        select(User, Membership, AuthSession)
        .join(
            Membership,
            Membership.user_id == User.id,
        )
        .join(
            AuthSession,
            (AuthSession.user_id == User.id) & (AuthSession.team_id == Membership.team_id),
        )
        .where(
            AuthSession.token_hash == digest(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
            User.active.is_(True),
            Membership.team_id == DEFAULT_TEAM_ID,
        )
    ).one_or_none()
    if row is None:
        raise ApiFailure(401, ErrorCode.SESSION_EXPIRED)
    return Actor(*row, token)


CurrentActor = Annotated[Actor, Depends(get_actor)]


def require(permission: Permission):
    def authorize(actor: CurrentActor) -> Actor:
        if not permitted(actor.membership.role, permission):
            raise ApiFailure(403, ErrorCode.ACCESS_DENIED)
        return actor

    return authorize
