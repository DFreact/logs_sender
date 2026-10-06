from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import lock_team
from app.api.schemas import UserCreate
from app.domain.enums import UserRole
from app.persistence.models import DEFAULT_TEAM_ID, Membership, User
from app.security.audit import AuditAction, record
from app.security.passwords import hash_password


def create_first_admin(db: Session, body: UserCreate) -> User:
    lock_team(db)
    if db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.team_id == DEFAULT_TEAM_ID,
        )
    ):
        raise RuntimeError("BOOTSTRAP_ALREADY_COMPLETED")
    user = User(
        username=body.username,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        active=True,
    )
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, team_id=DEFAULT_TEAM_ID, role=UserRole.ADMINISTRATOR))
    record(
        db,
        AuditAction.ADMIN_BOOTSTRAPPED,
        actor_id=user.id,
        entity_id=user.id,
        after={
            "username": user.username,
            "display_name": user.display_name,
            "role": UserRole.ADMINISTRATOR,
            "active": True,
        },
    )
    db.commit()
    return user
