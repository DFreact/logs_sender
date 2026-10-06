"""Run stage-2 checks on an isolated Compose project, then remove its test data."""

import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main(
    background=False,
    ingestion=False,
    sources=False,
    routing=False,
    channels=False,
    notifications=False,
    escalations=False,
    browser_only=False,
    digests=False,
):
    notifications = notifications or escalations or digests
    project = "eventhub-check-" + uuid4().hex[:8]
    port = os.environ.get("HUB_VERIFY_HTTP_PORT", "8081")
    database_port = os.environ.get("HUB_VERIFY_DB_PORT", "55439")
    env = {
        **os.environ,
        "HUB_HTTP_PORT": port,
        "HUB_SMTP_PORT": os.environ.get("HUB_VERIFY_SMTP_PORT", "2526"),
        "HUB_DOCKER_SUBNET": os.environ.get("HUB_VERIFY_SUBNET", "172.31.52.0/24"),
    }
    with tempfile.TemporaryDirectory(
        prefix="identity-", dir=ROOT / ".local"
    ) as directory:
        override = Path(directory) / "compose.yaml"
        override.write_text(f"""services:
  postgres:
    ports: ["127.0.0.1:{database_port}:5432"]
  api:
    environment:
      HUB_LOGIN_ACCOUNT_LIMIT: "1000"
      HUB_LOGIN_IP_LIMIT: "5000"
""")
        if not notifications:
            override.write_text(override.read_text() + """      HUB_OUTBOUND_HOSTS: '["192.0.2.10"]'
      HUB_OUTBOUND_NETWORKS: '["192.0.2.0/24"]'
""")
        if notifications:
            from delivery_checks import fixtures

            override.write_text(
                override.read_text() + fixtures(directory, env["HUB_DOCKER_SUBNET"])
            )
        compose = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            "infra/compose/compose.yaml",
            "-f",
            str(override),
        ]

        def run(*args, input_text=None):
            result = subprocess.run(
                [*compose, *args],
                cwd=ROOT,
                env=env,
                input=input_text,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                diagnostic = ROOT / ".local" / (project + "-command-error.log")
                descriptor = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    output.write(result.stderr)
                raise RuntimeError(f"COMPOSE_CHECK_FAILED:{args[0]}")
            return result.stdout

        try:
            print("Запуск отдельного стенда PostgreSQL…", flush=True)
            run("up", "-d", "--no-build", "--wait")
            from database_checks import verify as verify_database_privileges

            verify_database_privileges(run)
            credentials = {
                role: {"username": role, "password": secrets.token_urlsafe(24)}
                for role in ("admin", "operator", "viewer")
            }
            seed = """
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import DEFAULT_TEAM_ID, Membership, User
from app.api.schemas import UserCreate
from app.security.bootstrap import create_first_admin
from app.security.passwords import hash_password
engine = build_engine(Settings())
with build_session_factory(engine)() as db:
    create_first_admin(db, UserCreate(**credentials["admin"], display_name="Администратор"))
    for role, label in (("operator", "Оператор"), ("viewer", "Наблюдатель")):
        user = User(username=role, display_name=label,
                    password_hash=hash_password(credentials[role]["password"]))
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, team_id=DEFAULT_TEAM_ID, role=role.upper()))
    db.commit()
engine.dispose()
"""
            run(
                "exec",
                "-T",
                "api",
                "python",
                "-",
                input_text="credentials = " + repr(credentials) + "\n" + seed,
            )
            run(
                "exec",
                "-T",
                "api",
                "python",
                "-",
                input_text=(ROOT / "backend/tests/seed_events.py").read_text()
                + """
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.services.ingestion import store_for
settings = Settings()
engine = build_engine(settings)
seed_events(build_session_factory(engine), store_for(settings), settings)
engine.dispose()
""",
            )
            if ingestion:
                from ingestion_checks import verify as verify_ingestion

                verify_ingestion(
                    run, f"http://127.0.0.1:{port}", int(env["HUB_SMTP_PORT"])
                )
            if routing:
                from routing_checks import verify as verify_routing

                verify_routing(run, int(env["HUB_SMTP_PORT"]))
            if notifications:
                from delivery_checks import verify as verify_delivery

                verify_delivery(run, int(env["HUB_SMTP_PORT"]))
            if escalations:
                from escalation_checks import verify as verify_escalation

                verify_escalation(run, int(env["HUB_SMTP_PORT"]))
            if digests:
                from digest_checks import verify as verify_digests

                verify_digests(run, int(env["HUB_SMTP_PORT"]))
            target = Path(directory) / "credentials.json"
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(credentials, output)
            if background:
                from background_checks import verify

                verify(run, f"http://127.0.0.1:{port}")
            if not browser_only:
                test_env = {**os.environ, "HUB_TEST_POSTGRES_PORT": database_port}
                subprocess.run(
                    [
                        str(ROOT / ".venv/bin/pytest"),
                        "-q",
                        "tests/test_identity.py",
                        "tests/test_connections.py",
                        "tests/test_event_feed.py",
                        "tests/test_appearance.py",
                        "tests/test_retention.py",
                        *(["tests/test_digests.py"] if digests else []),
                        *(["tests/test_escalations.py"] if escalations else []),
                        *(
                            [
                                "tests/test_notifications.py",
                                "tests/test_delivery_transport.py",
                            ]
                            if notifications
                            else []
                        ),
                        *(["tests/test_sources.py"] if sources or routing else []),
                        *(["tests/test_routing.py"] if routing else []),
                        *(
                            ["tests/test_channels.py", "tests/test_output_transport.py", "tests/test_max.py"]
                            if channels
                            else []
                        ),
                        *(["tests/test_ingestion.py"] if ingestion else []),
                        *(
                            [
                                "tests/test_background.py",
                                "tests/test_storage.py",
                                "tests/test_health.py",
                            ]
                            if background
                            else []
                        ),
                    ],
                    cwd=ROOT / "backend",
                    env=test_env,
                    check=True,
                )
            if escalations:
                from escalation_checks import prepare_browser

                prepare_browser(run, int(env["HUB_SMTP_PORT"]))
            test_env = {
                **os.environ,
                "E2E_BASE_URL": f"http://127.0.0.1:{port}",
                "E2E_CREDENTIALS": str(target),
                **({"E2E_DELIVERY_FIXTURE": "1"} if notifications else {}),
                **({"E2E_ESCALATION_FIXTURE": "1"} if escalations else {}),
                **({"E2E_DIGEST_FIXTURE": "1"} if digests else {}),
            }
            subprocess.run(
                ["npm", "run", "test:e2e", "--", "--workers=2"],
                cwd=ROOT / "frontend",
                env=test_env,
                check=True,
            )
            print(
                "Проверки выбранного набора и собранного интерфейса пройдены.",
                flush=True,
            )
        except BaseException:
            # Keep private diagnostics before the disposable database is removed.
            diagnostic = ROOT / ".local" / (project + "-postgres.log")
            try:
                descriptor = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as target:
                    target.write(run("logs", "--no-color", "postgres"))
            except (OSError, RuntimeError):
                pass
            raise
        finally:
            # Only this invocation's unique project and disposable volume are removed.
            run("down", "--volumes", "--remove-orphans")
            print("Временный стенд и его данные удалены.", flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    parser.add_argument(
        "--browser-only",
        action="store_true",
        help="Prepare fixtures and run browser checks without repeating server pytest",
    )
    parser.add_argument("--ingestion", action="store_true")
    parser.add_argument("--sources", action="store_true")
    parser.add_argument("--routing", action="store_true")
    parser.add_argument("--channels", action="store_true")
    parser.add_argument("--escalations", action="store_true")
    parser.add_argument("--digests", action="store_true")
    parser.add_argument("--notifications", action="store_true")
    args = parser.parse_args()
    main(
        background=args.background,
        ingestion=args.ingestion,
        sources=args.sources,
        routing=args.routing,
        channels=args.channels,
        notifications=args.notifications,
        escalations=args.escalations,
        browser_only=args.browser_only,
        digests=args.digests,
    )
