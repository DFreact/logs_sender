from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.auth import audit_request, user_view
from app.api.dependencies import Actor, Database, require
from app.api.errors import ApiFailure, ErrorCode
from app.api.schemas import UserCreate, UserUpdate, UserView
from app.domain.enums import UserRole
from app.persistence.models import DEFAULT_TEAM_ID, AuthSession, Membership, User, utcnow
from app.security.audit import AuditAction, record
from app.security.passwords import hash_password
from app.security.permissions import Permission

router = APIRouter(prefix="/api/v1/users")
UserAdmin = Annotated[Actor, Depends(require(Permission.MANAGE_USERS))]


def snapshot(user: User, member: Membership):
    return {
        "username": user.username,
        "display_name": user.display_name,
        "role": member.role,
        "active": user.active,
    }


@router.get("")
def list_users(
    db: Database,
    actor: UserAdmin,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
):
    query = select(User, Membership).join(Membership).where(Membership.team_id == DEFAULT_TEAM_ID)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(User.username, User.id).offset(offset).limit(limit)).all()
    return {"items": [user_view(*row) for row in rows], "total": total}


@router.post("", status_code=201, response_model=UserView)
def create_user(body: UserCreate, request: Request, db: Database, actor: UserAdmin):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise ApiFailure(409, ErrorCode.USERNAME_TAKEN)
    user = User(
        username=body.username,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        active=True,
    )
    db.add(user)
    try:
        db.flush()
        member = Membership(user_id=user.id, team_id=DEFAULT_TEAM_ID, role=body.role)
        db.add(member)
        record(
            db,
            AuditAction.USER_CREATED,
            actor_id=actor.user.id,
            entity_id=user.id,
            after=snapshot(user, member),
            **audit_request(request),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiFailure(409, ErrorCode.USERNAME_TAKEN) from None
    return user_view(user, member)


@router.patch("/{user_id}", response_model=UserView)
def update_user(user_id: UUID, body: UserUpdate, request: Request, db: Database, actor: UserAdmin):
    row = db.execute(
        select(User, Membership)
        .join(Membership)
        .where(
            User.id == user_id,
            Membership.team_id == DEFAULT_TEAM_ID,
        )
    ).one_or_none()
    if row is None:
        raise ApiFailure(404, ErrorCode.RESOURCE_NOT_FOUND)
    user, member = row
    if user.version != body.version:
        raise ApiFailure(409, ErrorCode.VERSION_CONFLICT)
    if (
        user.active
        and member.role == UserRole.ADMINISTRATOR
        and (not body.active or body.role != UserRole.ADMINISTRATOR)
    ):
        admins = db.scalar(
            select(func.count())
            .select_from(User)
            .join(Membership)
            .where(
                User.active.is_(True),
                Membership.team_id == DEFAULT_TEAM_ID,
                Membership.role == UserRole.ADMINISTRATOR,
            )
        )
        if admins <= 1:
            raise ApiFailure(409, ErrorCode.LAST_ADMINISTRATOR)
    before = snapshot(user, member)
    revoke = body.active != user.active or body.role != member.role or body.password is not None
    user.display_name, user.active, member.role = body.display_name, body.active, body.role
    user.version += 1
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        record(
            db,
            AuditAction.USER_PASSWORD_CHANGED,
            actor_id=actor.user.id,
            entity_id=user.id,
            after={"password_changed": True},
            **audit_request(request),
        )
    if revoke:
        db.execute(
            update(AuthSession).where(AuthSession.user_id == user.id).values(revoked_at=utcnow())
        )
    record(
        db,
        AuditAction.USER_UPDATED,
        actor_id=actor.user.id,
        entity_id=user.id,
        before=before,
        after=snapshot(user, member),
        **audit_request(request),
    )
    db.commit()
    return user_view(user, member)
