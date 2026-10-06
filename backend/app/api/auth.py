from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update

from app.api.dependencies import CurrentActor, Database, lock_team
from app.api.errors import ApiFailure, ErrorCode
from app.api.schemas import LoginInput, MeView, PasswordChange, UserView
from app.persistence.models import DEFAULT_TEAM_ID, AuthSession, Membership, User, utcnow
from app.security.audit import AuditAction, record
from app.security.passwords import dummy_hash, hash_password, hasher, verify_password
from app.security.permissions import ROLE_PERMISSIONS
from app.security.rate_limit import login_allowed
from app.security.tokens import (
    TOKEN_RE,
    check_preauth,
    client_ip,
    csrf_for_session,
    digest,
    new_token,
    preauth_pair,
)

router = APIRouter(prefix="/api/v1/auth")


def user_view(user: User, membership: Membership) -> UserView:
    return UserView(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        active=user.active,
        role=membership.role,
        version=user.version,
        created_at=user.created_at,
    )


def audit_request(request: Request) -> dict:
    return {
        "request_id": request.state.request_id,
        "request_ip": client_ip(request, request.app.state.settings),
    }


def me_view(
    request: Request, user: User, membership: Membership, session: AuthSession, token: str
) -> MeView:
    return MeView(
        user=user_view(user, membership),
        permissions=sorted(ROLE_PERMISSIONS[membership.role]),
        csrf_token=csrf_for_session(request.app.state.settings, token),
        expires_at=session.expires_at,
    )


@router.get("/csrf")
def csrf(request: Request, response: Response):
    settings = request.app.state.settings
    cookie, token = preauth_pair(settings)
    response.set_cookie(
        settings.preauth_cookie,
        cookie,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="strict",
        max_age=600,
        path="/",
    )
    return {"csrf_token": token}


@router.post("/login", response_model=MeView)
def login(body: LoginInput, request: Request, response: Response, db: Database):
    settings = request.app.state.settings
    check_preauth(request, settings)
    if not login_allowed(db, settings, body.username, client_ip(request, settings)):
        raise ApiFailure(429, ErrorCode.RATE_LIMITED)
    lock_team(db)
    row = db.execute(
        select(User, Membership)
        .join(Membership)
        .where(
            User.username == body.username,
            Membership.team_id == DEFAULT_TEAM_ID,
        )
    ).one_or_none()
    encoded = row.User.password_hash if row else dummy_hash
    verified = verify_password(encoded, body.password)
    if row is None or not verified or not row.User.active:
        record(db, AuditAction.LOGIN_FAILED, **audit_request(request))
        db.commit()
        raise ApiFailure(401, ErrorCode.INVALID_CREDENTIALS)
    user, membership = row
    if hasher.check_needs_rehash(encoded):
        user.password_hash = hash_password(body.password)
    old = request.cookies.get(settings.session_cookie, "")
    if TOKEN_RE.fullmatch(old):
        db.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == digest(old))
            .values(revoked_at=utcnow())
        )
    token = new_token()
    session = AuthSession(
        token_hash=digest(token),
        user_id=user.id,
        team_id=DEFAULT_TEAM_ID,
        expires_at=utcnow() + timedelta(seconds=settings.session_seconds),
    )
    db.add(session)
    record(
        db,
        AuditAction.LOGIN_SUCCEEDED,
        actor_id=user.id,
        entity_id=user.id,
        **audit_request(request),
    )
    db.commit()
    response.set_cookie(
        settings.session_cookie,
        token,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="strict",
        max_age=settings.session_seconds,
        path="/",
    )
    response.delete_cookie(
        settings.preauth_cookie,
        path="/",
        secure=settings.secure_cookie,
        httponly=True,
        samesite="strict",
    )
    return me_view(request, user, membership, session, token)


@router.get("/me", response_model=MeView)
def me(request: Request, actor: CurrentActor):
    return me_view(request, actor.user, actor.membership, actor.session, actor.token)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Database, actor: CurrentActor):
    actor.session.revoked_at = utcnow()
    record(
        db,
        AuditAction.LOGOUT,
        actor_id=actor.user.id,
        entity_id=actor.user.id,
        **audit_request(request),
    )
    db.commit()
    settings = request.app.state.settings
    response.delete_cookie(
        settings.session_cookie,
        path="/",
        secure=settings.secure_cookie,
        httponly=True,
        samesite="strict",
    )


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChange, request: Request, response: Response, db: Database, actor: CurrentActor
):
    # Reuse persistent throttling to bound expensive password verification.
    # This commit releases the team lock; re-read authorization under a new lock below.
    from app.api.dependencies import get_actor

    if not login_allowed(
        db,
        request.app.state.settings,
        actor.user.username,
        client_ip(request, request.app.state.settings),
    ):
        raise ApiFailure(429, ErrorCode.RATE_LIMITED)
    db.expire_all()
    actor = get_actor(request, db)
    if not verify_password(actor.user.password_hash, body.current_password):
        raise ApiFailure(400, ErrorCode.CURRENT_PASSWORD_INVALID)
    actor.user.password_hash = hash_password(body.new_password)
    actor.user.version += 1
    db.execute(
        update(AuthSession).where(AuthSession.user_id == actor.user.id).values(revoked_at=utcnow())
    )
    record(
        db,
        AuditAction.USER_PASSWORD_CHANGED,
        actor_id=actor.user.id,
        entity_id=actor.user.id,
        after={"password_changed": True},
        **audit_request(request),
    )
    db.commit()
    settings = request.app.state.settings
    response.delete_cookie(
        settings.session_cookie,
        path="/",
        secure=settings.secure_cookie,
        httponly=True,
        samesite="strict",
    )
