"""Isolated browser-test server. Never use these accounts in a deployed instance."""

import json
import os
import secrets
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from pydantic import SecretStr
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.schemas import UserCreate  # noqa: E402
from app.main import create_app  # noqa: E402
from app.persistence.database import Base, build_session_factory  # noqa: E402
from app.persistence.models import DEFAULT_TEAM_ID, Membership, Team, User  # noqa: E402
from app.security.bootstrap import create_first_admin  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402
from app.settings import Settings  # noqa: E402


def main():
    credentials = {
        role: {"username": role, "password": secrets.token_urlsafe(24)}
        for role in ("admin", "operator", "viewer")
    }
    target = Path(__file__).resolve().parents[2] / ".local" / "e2e-credentials.json"
    target.parent.mkdir(mode=0o700, exist_ok=True)
    with TemporaryDirectory(prefix="eventhub-e2e-") as directory:
        engine = create_engine(f"sqlite:///{directory}/test.db")
        Base.metadata.create_all(engine)
        factory = build_session_factory(engine)
        with factory() as db:
            db.add(Team(id=DEFAULT_TEAM_ID, name="default"))
            db.commit()
            create_first_admin(db, UserCreate(**credentials["admin"], display_name="Администратор"))
            for role, label in (("operator", "Оператор"), ("viewer", "Наблюдатель")):
                user = User(
                    username=role,
                    display_name=label,
                    password_hash=hash_password(credentials[role]["password"]),
                )
                db.add(user)
                db.flush()
                db.add(Membership(user_id=user.id, team_id=DEFAULT_TEAM_ID, role=role.upper()))
            db.commit()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(credentials, output)
        settings = Settings(
            auth_secret=SecretStr(secrets.token_urlsafe(48)),
            channel_secret=SecretStr(secrets.token_urlsafe(48)),
            allow_insecure_local_http=True,
            allowed_origins=["http://127.0.0.1:4173"],
            outbound_hosts=["192.0.2.10"],
            outbound_networks=["192.0.2.0/24"],
            login_account_limit=1000,
            login_ip_limit=5000,
        )
        from seed_events import seed_events

        from app.services.ingestion import store_for
        settings.storage_root = Path(directory) / 'blobs'
        settings.storage_min_free_bytes = 0
        store = store_for(settings)
        store.initialize()
        seed_events(factory, store, settings)
        try:
            uvicorn.run(
                create_app(settings, factory),
                host="127.0.0.1",
                port=8000,
                access_log=False,
                log_level="warning",
            )
        finally:
            target.unlink(missing_ok=True)
            engine.dispose()


if __name__ == "__main__":
    main()
