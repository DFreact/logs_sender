import time

from sqlalchemy import case, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.persistence.models import AuthSession, LoginLimit, utcnow
from app.security.tokens import signed
from app.settings import Settings


def consume(db: Session, key: str, window: int, limit: int) -> bool:
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(LoginLimit).values(key=key, window_start=window, attempts=1)
    statement = statement.on_conflict_do_update(
        index_elements=[LoginLimit.key],
        set_={
            "attempts": case((LoginLimit.window_start != window, 1), else_=LoginLimit.attempts + 1),
            "window_start": window,
        },
        where=or_(LoginLimit.window_start != window, LoginLimit.attempts < limit),
    ).returning(LoginLimit.attempts)
    return db.execute(statement).scalar_one_or_none() is not None


def login_allowed(db: Session, settings: Settings, username: str, address: str) -> bool:
    window = int(time.time()) // settings.login_window_seconds * settings.login_window_seconds
    old_keys = select(LoginLimit.key).where(LoginLimit.window_start < window).limit(100)
    db.execute(delete(LoginLimit).where(LoginLimit.key.in_(old_keys)))
    expired = select(AuthSession.token_hash).where(AuthSession.expires_at < utcnow()).limit(100)
    db.execute(delete(AuthSession).where(AuthSession.token_hash.in_(expired)))
    allowed = consume(db, signed(settings, "ip:" + address), window, settings.login_ip_limit)
    if allowed:
        allowed = consume(
            db, signed(settings, "account:" + username), window, settings.login_account_limit
        )
    # Persist reservations even when credentials or the second bucket fail.
    db.commit()
    return allowed
