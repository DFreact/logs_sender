import os
import secrets
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, event, text

from app.api.schemas import UserCreate
from app.main import create_app
from app.persistence.database import Base, build_session_factory
from app.persistence.models import DEFAULT_TEAM_ID, StorageScan, Team
from app.security.bootstrap import create_first_admin
from app.settings import Settings
from app.storage.files import BlobStore

ORIGIN = "https://test"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def setup(tmp_path):
    port = os.environ.get("HUB_TEST_POSTGRES_PORT")
    admin_engine = None
    if port:
        config = Settings(
            db_port=int(port),
            db_user="eventhub",
            db_password_file=Path(__file__).resolve().parents[2] / ".local/secrets/db_admin_password",
        )
        admin_engine = create_engine(config.database_url(), hide_parameters=True)
        schema = "test_" + uuid4().hex
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            config.database_url(),
            hide_parameters=True,
            connect_args={"options": f"-csearch_path={schema}"},
        )
    else:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'identity.db'}", connect_args={"check_same_thread": False}
        )

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    password = secrets.token_urlsafe(24)
    with factory() as db:
        db.add(Team(id=DEFAULT_TEAM_ID, name="default"))
        db.commit()
        user = create_first_admin(
            db, UserCreate(username="admin", display_name="Администратор", password=password)
        )
        user_id = user.id
    settings = Settings(
        auth_secret=SecretStr(secrets.token_urlsafe(48)),
        channel_secret=SecretStr(secrets.token_urlsafe(48)),
        allowed_origins=[ORIGIN],
        login_account_limit=100,
        login_ip_limit=1000,
        outbound_hosts=["192.0.2.10"], outbound_networks=["192.0.2.0/24"],
    )
    app = create_app(settings, factory)
    try:
        yield app, factory, password, user_id, settings
    finally:
        engine.dispose()
        if admin_engine:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin_engine.dispose()


@pytest.fixture
def background(setup, tmp_path):
    _, factory, _, user_id, settings = setup
    settings.storage_root = tmp_path / "blobs"
    settings.storage_min_free_bytes = 0
    store = BlobStore(settings.storage_root, settings.blob_max_bytes)
    store.initialize()
    with factory.begin() as db:
        db.add(StorageScan(id=1, shard=0, cursor=""))
    return factory, store, settings, user_id
