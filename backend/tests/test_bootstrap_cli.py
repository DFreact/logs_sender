import secrets
import sys

from sqlalchemy import create_engine, select

from app import cli
from app.persistence.database import Base, build_session_factory
from app.persistence.models import DEFAULT_TEAM_ID, AuditEntry, Team, User
from app.security.passwords import verify_password


def test_interactive_bootstrap_creates_admin_once_without_echoing_password(
    tmp_path, monkeypatch, capsys
):
    import pytest

    engine = create_engine(f"sqlite:///{tmp_path / 'bootstrap.db'}")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    with factory() as db:
        db.add(Team(id=DEFAULT_TEAM_ID, name="default"))
        db.commit()
    password = secrets.token_urlsafe(24)
    monkeypatch.setattr(cli, "build_engine", lambda _: engine)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        sys,
        "argv",
        ["app.cli", "create-admin", "--username", "admin", "--display-name", "Администратор"],
    )
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: password)
    cli.main()
    output = capsys.readouterr().out
    assert "Первый администратор создан" in output and password not in output
    with factory() as db:
        user = db.scalar(select(User))
        assert verify_password(user.password_hash, password)
        assert db.scalar(select(AuditEntry.action)) == "ADMIN_BOOTSTRAPPED"
    with pytest.raises(SystemExit, match="Первый администратор уже создан"):
        cli.main()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit, match="интерактивном терминале"):
        cli.main()
    engine.dispose()
