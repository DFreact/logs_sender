"""Restore exercise restricted to fresh, disposable offline projects."""

import argparse
import json
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/release"))
import admin  # noqa: E402

PRELUDE = """
import json
from sqlalchemy import select, func
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import *
settings = Settings()
engine = build_engine(settings)
factory = build_session_factory(engine)
"""


def verify(kit, output):
    if output.exists():
        raise RuntimeError("REPORT_ALREADY_EXISTS")
    kit = kit.resolve()
    admin.HERE = kit
    manifest = admin.verified(kit, "release")
    label = "eventhub-offline-" + uuid4().hex[:8]
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "release_manifest_sha256": admin.digest(kit / "manifest.json"),
        "images": manifest["images"],
    }
    with tempfile.TemporaryDirectory(prefix="offline-check-", dir=ROOT / ".local") as temporary:
        base = Path(temporary)
        first, second = base / "first", base / "second"
        backup = base / "backup"

        def command(*args):
            started = time.monotonic()
            result = subprocess.run(
                [sys.executable, "-c", "import admin; admin.main()", *map(str, args)],
                cwd=kit,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                diagnostic = ROOT / ".local/offline-check-error.log"
                diagnostic.touch(mode=0o600, exist_ok=True)
                diagnostic.write_bytes(result.stderr)
                raise admin.Failure("failed")
            return round(time.monotonic() - started, 3)

        def python(directory, code, running=True):
            args = (
                ("exec", "-T", "api", "python", "-")
                if running
                else ("run", "--rm", "--no-deps", "-T", "--pull", "never", "api", "python", "-")
            )
            return admin.compose(directory, *args, input=(PRELUDE + code).encode()).decode().strip()

        try:
            print("Проверка комплекта и загрузка образов…", flush=True)
            command("verify")
            report["load_seconds"] = command("load")
            report["install_seconds"] = command(
                "install",
                "--directory",
                first,
                "--project",
                label + "-a",
                "--http-port",
                "8084",
                "--smtp-port",
                "2529",
                "--subnet",
                "172.31.56.0/24",
                "--isolated",
            )
            from database_checks import verify as verify_database_privileges

            def security_run(directory):
                def invoke(*args, input_text=None):
                    return admin.compose(
                        directory, *args, input=input_text.encode() if input_text else None
                    ).decode()

                return invoke

            report["initial_privileges"] = verify_database_privileges(security_run(first))
            network = json.loads(admin.run(["docker", "network", "inspect", label + "-a_default"]))[
                0
            ]
            assert network["Internal"] is True
            python(
                first,
                """
import socket
try:
    socket.create_connection(('1.1.1.1', 443), timeout=2)
except OSError:
    pass
else:
    raise AssertionError('EXTERNAL_NETWORK_REACHABLE')
""",
            )
            report["external_network_blocked"] = True
            print("Установка готова без внешней сети. Создание контрольных данных…", flush=True)
            credentials = {
                "password": secrets.token_urlsafe(24),
                "channel_secret": secrets.token_urlsafe(24),
            }
            python(
                first,
                "credentials = "
                + repr(credentials)
                + """
from datetime import timedelta
from app.api.schemas import UserCreate
from app.security.bootstrap import create_first_admin
from app.security.channel_secrets import protect

with factory() as db:
    created = create_first_admin(
        db,
        UserCreate(
            username="restore-admin",
            display_name="Проверка восстановления",
            password=credentials["password"],
        ),
    )
with factory.begin() as db:
    db.add(AuthSession(token_hash='1' * 64, user_id=created.id,
        team_id=DEFAULT_TEAM_ID, expires_at=utcnow() + timedelta(hours=1)))
    team = db.get(Team, DEFAULT_TEAM_ID)
    team.appearance_palette = "violet"
    team.appearance_mode = "dark"
    channel = NotificationChannel(
        id=uuid4(),
        team_id=DEFAULT_TEAM_ID,
        name="Проверка копии",
        kind="WEBHOOK",
        enabled=False,
        configuration={},
    )
    channel.secret_ciphertext = protect(
        settings, DEFAULT_TEAM_ID, channel.id, credentials["channel_secret"]
    )
    db.add(channel)
""",
            )
            # Leave accepted originals pending: recovery must reconstruct the queue
            # from PostgreSQL while Redis starts empty.
            admin.compose(first, "stop", "ingestion")
            raw_ids = json.loads(
                python(
                    first,
                    r"""
import urllib.request
import smtplib
from app.services.ingestion import issue_key

with factory.begin() as db:
    _, token = issue_key(db, "Проверка автономного восстановления")
ids = []
for index in range(20):
    request = urllib.request.Request(
        "http://web:8080/api/v1/ingest",
        data=json.dumps(
            {"subject": "Копия " + str(index), "body": "Исходное сообщение " + str(index)}
        ).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Idempotency-Key": "restore-" + str(index),
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 202
        ids.append(json.load(response)["receipt_id"])
with smtplib.SMTP("smtp", 2525, timeout=20) as client:
    client.sendmail(
        "check@example.org",
        ["events@localhost"],
        b'Subject: archive-with-attachment\r\nMIME-Version: 1.0\r\n'
        b'Content-Type: multipart/mixed; boundary="test"\r\n\r\n'
        b'--test\r\nContent-Type: text/plain\r\n\r\noriginal\r\n'
        b'--test\r\nContent-Type: application/octet-stream\r\n'
        b'Content-Disposition: attachment; filename="example.bin"\r\n'
        b'Content-Transfer-Encoding: base64\r\n\r\nAQIDBA==\r\n--test--\r\n',
    )
print(json.dumps(ids))
""",
                )
            )
            assert len(set(raw_ids)) == 20
            report["backup_seconds_including_resume"] = command(
                "backup", "--directory", first, "--output", backup
            )
            saved = admin.verified(backup, "backup")
            report["copy_checkpoint"] = saved["checkpoint"]
            report["quiesce_and_copy_seconds"] = saved["seconds"]
            report["backup_bytes"] = {
                name: (backup / name).stat().st_size for name in sorted(admin.BACKUP_FILES)
            }
            assert (
                "ingestion"
                not in admin.compose(first, "ps", "--status", "running", "--services")
                .decode()
                .split()
            )
            report["previously_stopped_worker_remains_stopped"] = True
            # Refuse corrupted copies before creating a destination.
            key = (backup / "channel_secret").read_bytes()
            (backup / "channel_secret").write_bytes(b"corrupted")
            try:
                command(
                    "restore",
                    "--backup",
                    backup,
                    "--directory",
                    base / "rejected",
                    "--project",
                    label + "-bad",
                )
                raise AssertionError("CORRUPTED_BACKUP_ACCEPTED")
            except admin.Failure:
                assert not (base / "rejected").exists()
            (backup / "channel_secret").write_bytes(key)
            print(
                "Копия согласована; повреждение ключа обнаруживается до восстановления…", flush=True
            )
            started = time.monotonic()
            report["restore_seconds_before_start"] = command(
                "restore",
                "--backup",
                backup,
                "--directory",
                second,
                "--project",
                label + "-b",
                "--http-port",
                "8085",
                "--smtp-port",
                "2530",
                "--subnet",
                "172.31.57.0/24",
                "--isolated",
            )
            assert admin.compose(
                second, "ps", "--status", "running", "--services"
            ).decode().split() == ["postgres"]
            assert (first / ".secrets/channel_secret").read_bytes() == (
                second / ".secrets/channel_secret"
            ).read_bytes()
            for name in (
                "auth_secret",
                "db_password",
                "db_admin_password",
                "db_migration_password",
                "redis_password",
            ):
                assert (first / ".secrets" / name).read_bytes() != (
                    second / ".secrets" / name
                ).read_bytes()
            python(
                second,
                "credentials = "
                + repr(credentials)
                + """
from app.security.channel_secrets import reveal
from app.security.passwords import verify_password

with factory() as db:
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
    assert db.scalar(select(func.count()).select_from(RawMessage)) == 21
    assert (
        db.scalar(select(func.count()).select_from(RawMessage).where(RawMessage.state == "PENDING"))
        == 21
    )
    team = db.get(Team, DEFAULT_TEAM_ID)
    assert (team.appearance_palette, team.appearance_mode) == ("violet", "dark")
    user = db.scalar(select(User).where(User.username == "restore-admin"))
    assert verify_password(user.password_hash, credentials["password"])
    channel = db.scalar(select(NotificationChannel))
    assert (
        reveal(settings, channel.team_id, channel.id, channel.secret_ciphertext)
        == credentials["channel_secret"]
    )
""",
                running=False,
            )
            command("start", "--directory", second)
            deadline = time.monotonic() + 120
            while True:
                counts = json.loads(
                    python(
                        second,
                        """
with factory() as db:
    def count(model, *conditions):
        return db.scalar(select(func.count()).select_from(model).where(*conditions))
    print(json.dumps({
        'pending': count(RawMessage, RawMessage.state == 'PENDING'),
        'raw': count(RawMessage), 'occurrences': count(EventOccurrence),
        'attachments': count(Attachment),
    }))
""",
                    )
                )
                if counts["pending"] == 0:
                    break
                if time.monotonic() > deadline:
                    raise AssertionError("QUEUE_DID_NOT_RECOVER")
                time.sleep(1)
            assert counts == {"pending": 0, "raw": 21, "occurrences": 21, "attachments": 1}
            restored_ids = set(
                json.loads(
                    python(
                        second,
                        """
with factory() as db:
    print(json.dumps([str(value) for value in db.scalars(select(RawMessage.id))]))
""",
                    )
                )
            )
            assert set(raw_ids) <= restored_ids
            report["rest_receipt_ids_preserved"] = True
            admin.storage(second, "check")
            python(
                second,
                "credentials = "
                + repr(credentials)
                + r"""
import http.cookiejar
import re
import urllib.request
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
base = 'http://web:8080'
with opener.open(base + '/') as response:
    page = response.read().decode()
    assert 'lang="ru"' in page and 'Центр событий' in page
    assert "default-src 'self'" in response.headers['Content-Security-Policy']
    assert not re.search(r'(?:src|href)="https?://', page)
with opener.open(base + '/api/v1/auth/csrf') as response:
    csrf = json.load(response)['csrf_token']
request = urllib.request.Request(base + '/api/v1/auth/login',
    data=json.dumps({'username':'restore-admin', 'password':credentials['password']}).encode(),
    headers={'Content-Type':'application/json', 'X-CSRF-Token':csrf,
             'Origin':'http://127.0.0.1:8085'})
with opener.open(request) as response:
    assert json.load(response)['user']['role'] == 'ADMINISTRATOR'
""",
            )
            report["login_and_russian_html_after_restore"] = True
            report["old_sessions_removed"] = True
            report["recovery_through_queue_seconds"] = round(time.monotonic() - started, 3)
            report["restored_counts"] = counts
            report["encrypted_channel_and_appearance_restored"] = True
            report["redis_queue_rebuilt"] = True
            report["delivery_stopped_until_explicit_start"] = True
            report["secret_rotation_verified"] = True
            report["restored_privileges"] = verify_database_privileges(security_run(second))
            report["corrupt_backup_rejected"] = True
            report["kit_size_bytes"] = sum(
                path.stat().st_size for path in kit.iterdir() if path.is_file()
            )
            admin.write_json(output, report)
            print(
                "Восстановление файлов, ключа каналов, оформления и очереди: пройдено.", flush=True
            )
        finally:
            # Only these randomly-named disposable installations may be removed.
            for directory in (first, second):
                if (directory / "installation.json").exists():
                    admin.compose(directory, "down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.kit, args.output)
