import asyncio
import json
import smtplib
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.message import EmailMessage
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_identity import ORIGIN, create_user, headers, login

from app.adapters.input.smtp import Receiver, RestrictedSMTP, tls_context
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    AdapterInstallation,
    Attachment,
    BlobRecord,
    Event,
    EventOccurrence,
    IngestCredential,
    RawMessage,
    Team,
    WorkItem,
)
from app.security.tokens import digest
from app.services.ingestion import issue_key, receive
from app.services.normalization import parse
from app.workers.execution import execute
from app.workers.queue import clock


@pytest.fixture
def ingest(background):
    factory, store, settings, _ = background
    with factory.begin() as db:
        identifier, token = issue_key(db, "Тестовый отправитель")
    return factory, store, settings, identifier, token


def process(ingest):
    factory, store, settings, _, _ = ingest
    with factory() as db:
        identifiers = db.scalars(select(WorkItem.id).where(WorkItem.kind == "NORMALIZE")).all()
    for identifier in identifiers:
        execute(factory, store, settings, str(identifier))
    return identifiers


def mail():
    message = EmailMessage()
    message["From"] = "Система <sender@example.org>"
    message["To"] = "events@localhost"
    message["Subject"] = "Ошибка резервного копирования"
    message.set_content("Проверьте место на диске.")
    message.add_alternative(
        '<script>secret-script</script><img src="https://tracker/">Текст', subtype="html"
    )
    message.add_attachment(
        b"attachment-original",
        maintype="application",
        subtype="octet-stream",
        filename="../../report.bin",
    )
    return message.as_bytes(policy=policy.SMTP)


@pytest.mark.anyio
async def test_rest_idempotency_replay_and_reader_permissions(setup, ingest):
    app, factory, password, _, settings = setup
    _, store, _, key_id, token = ingest
    auth = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Idempotency-Key": "one",
    }
    payload = json.dumps(
        {"subject": "Сбой диска", "body": "Проверьте накопитель", "severity": "CRITICAL"},
        ensure_ascii=False,
    ).encode()
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        first = await client.post("/api/v1/ingest", headers=auth, content=payload)
        assert first.status_code == 202, first.text
        assert (
            await client.post("/api/v1/ingest", headers=auth, content=payload)
        ).json() == first.json()
        assert (await client.post("/api/v1/ingest", headers=auth, content=b"[]")).status_code == 409
        assert (await client.get("/api/v1/events", headers=auth)).status_code == 401
        receipt_id = first.json()["receipt_id"]
        assert (await client.get(f"/api/v1/ingest/{receipt_id}", headers=auth)).json()[
            "state"
        ] == "PENDING"
        for task_id in process(ingest):
            execute(factory, store, settings, str(task_id))
        status = (await client.get(f"/api/v1/ingest/{receipt_id}", headers=auth)).json()
        assert status["state"] == "SUCCEEDED"
        event_id = status["event_id"]
        assert (await client.get(f"/api/v1/events/{event_id}")).status_code == 401
        for role in ("ADMINISTRATOR", "OPERATOR", "VIEWER"):
            result = await login(client, password)
            if role != "ADMINISTRATOR":
                user, pw = await create_user(
                    client, headers(result), username=role.lower(), role=role
                )
                await login(client, pw, user["username"])
            response = await client.get("/api/v1/events?severity=CRITICAL&q=Сбой&adapter=REST")
            assert response.status_code == 200 and len(response.json()["items"]) == 1
            item = (await client.get(f"/api/v1/events/{event_id}")).json()
            assert item["body"] == "Проверьте накопитель" and item["source_id"] is None
            assert len(item["occurrences"]) == 1
            raw = await client.get(f"/api/v1/events/{event_id}/raw/{receipt_id}")
            assert (
                raw.content == payload and raw.headers["content-type"] == "application/octet-stream"
            )
            assert raw.headers["content-disposition"].startswith("attachment;")
            assert (
                await client.get(f"/api/v1/events/{uuid4()}/raw/{receipt_id}")
            ).status_code == 404
            assert (await client.get("/api/v1/events?status=RESOLVED")).json()["items"] == []
        with factory.begin() as db:
            db.get(IngestCredential, key_id).revoked_at = clock(db)
        assert (
            await client.post("/api/v1/ingest", headers=auth, content=payload)
        ).status_code == 401
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        assert db.scalar(select(func.count()).select_from(RawMessage)) == 1


@pytest.mark.anyio
async def test_limits_no_ack_on_storage_or_database_failure(setup, ingest, monkeypatch):
    app, factory, _, _, settings = setup
    _, _, _, _, token = ingest
    auth = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        for body, status in [
            (b'{"bad":}', 400),
            (b'"\xff"', 400),
            (b"NaN", 400),
            (b"[" * 21 + b"0" + b"]" * 21, 413),
        ]:
            assert (
                await client.post("/api/v1/ingest", headers=auth, content=body)
            ).status_code == status
        assert (
            await client.post(
                "/api/v1/ingest", headers={**auth, "Content-Type": "text/html"}, content=b"x"
            )
        ).status_code == 415
        settings.ingest_max_bytes = 20
        assert (
            await client.post("/api/v1/ingest", headers=auth, content=b'"' + b"x" * 30 + b'"')
        ).status_code == 413
        settings.storage_min_free_bytes = 10**30
        response = await client.post("/api/v1/ingest", headers=auth, content=b"{}")
        assert response.status_code == 503
        assert "secret" not in response.text and "Traceback" not in response.text
        settings.storage_min_free_bytes = 0
        from app.services import ingestion

        def fail(*args, **kwargs):
            from sqlalchemy.exc import OperationalError

            raise OperationalError("secret-sql", {}, Exception("password"))

        monkeypatch.setattr(ingestion, "enqueue", fail)
        assert (await client.post("/api/v1/ingest", headers=auth, content=b"{}")).status_code == 503
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(RawMessage)) == 0
        assert db.scalar(select(func.count()).select_from(WorkItem)) == 0
        assert not db.scalars(select(BlobRecord).where(BlobRecord.state == "READY")).all()


@pytest.mark.anyio
async def test_arbitrary_json_text_adapter_visibility_and_rate(setup, ingest):
    app, factory, _, _, settings = setup
    _, _, _, _, token = ingest
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        auth = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        for body in [b"null", b"42", b"[]", b'{"unrelated":"ok","severity":[]}']:
            assert (
                await client.post("/api/v1/ingest", headers=auth, content=body)
            ).status_code == 202
        auth["Content-Type"] = "text/plain"
        assert (
            await client.post("/api/v1/ingest", headers=auth, content="Произвольный текст".encode())
        ).status_code == 202
        process(ingest)
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Event)) == 5
        with factory.begin() as db:
            db.scalar(select(AdapterInstallation)).visible = False
        assert (await client.post("/api/v1/ingest", headers=auth, content=b"x")).status_code == 202
        with factory.begin() as db:
            db.scalar(select(AdapterInstallation)).enabled = False
        assert (await client.post("/api/v1/ingest", headers=auth, content=b"x")).status_code == 503
        settings.ingest_per_minute = 1
        assert (await client.post("/api/v1/ingest", headers=auth, content=b"x")).status_code == 429


def test_mime_original_attachments_and_replay(ingest):
    factory, store, settings, _, _ = ingest
    payload = mail()
    receipt = receive(
        factory,
        store,
        payload,
        "message/rfc822",
        kind="SMTP",
        envelope_sender="envelope@example.org",
        recipients=["events@localhost"],
    )
    for task in process(ingest):
        execute(factory, store, settings, str(task))
    with factory() as db:
        raw = db.get(RawMessage, receipt)
        assert raw.state == "NORMALIZED"
        event = db.scalar(select(Event))
        assert event.subject == "Ошибка резервного копирования" and "Проверьте" in event.body
        assert event.sender != raw.envelope_sender
        attachment = db.scalar(select(Attachment))
        assert attachment.filename == "report.bin"
        blob = db.get(BlobRecord, attachment.blob_id)
        assert store.read(blob.id, blob.size, blob.checksum) == b"attachment-original"
        blob = db.get(BlobRecord, raw.blob_id)
        assert store.read(blob.id, blob.size, blob.checksum) == payload
        assert db.scalar(select(func.count()).select_from(EventOccurrence)) == 1
        assert db.scalar(select(func.count()).select_from(Attachment)) == 1


def test_html_damaged_mime_bounded_previews():
    result = parse(
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b'<script>steal()</script><img src="https://tracker">Useful<b> text</b>',
        "message/rfc822",
    )
    assert result.body == "Useful text"
    assert "<" not in result.body and "steal" not in result.body and "tracker" not in result.body
    assert parse(
        b'Content-Type: multipart/mixed; boundary="missing"\r\n\r\ncorrupted', "message/rfc822"
    ).limited
    assert parse(b"--part\n" * 201, "message/rfc822").limited
    result = parse(b"Content-Type: text/plain\n\n" + b"x" * 70000, "message/rfc822")
    assert len(result.body) == 65536 and result.limited
    result = parse(b'{"subject":"\\ud800\\u0000", "body":"ok"}', "application/json")
    assert "\x00" not in result.subject


@pytest.mark.anyio
async def test_real_smtp_no_relay_limits_and_no_success_on_failed_commit(ingest, monkeypatch):
    factory, store, settings, _, _ = ingest
    settings.smtp_allow_plaintext = True
    settings.ingest_max_bytes = 2000
    loop = asyncio.get_running_loop()
    handler, connections = Receiver(factory, store, settings), set()
    server = await loop.create_server(
        lambda: RestrictedSMTP(
            handler,
            connections,
            settings,
            data_size_limit=settings.ingest_max_bytes,
            timeout=2,
            decode_data=False,
        ),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]

    def send(body, recipient="events@localhost"):
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            return client.sendmail("sender@example.org", [recipient], body)

    try:
        assert await asyncio.to_thread(send, mail()) == {}
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(RawMessage)) == 1
        with pytest.raises(smtplib.SMTPRecipientsRefused):
            await asyncio.to_thread(send, b"x", "external@example.org")
        with pytest.raises(smtplib.SMTPSenderRefused):
            await asyncio.to_thread(send, b"x" * 3000)
        from app.adapters.input import smtp

        def fail(*args, **kwargs):
            raise OSError("secret password")

        monkeypatch.setattr(smtp, "receive", fail)
        with pytest.raises(smtplib.SMTPDataError) as error:
            await asyncio.to_thread(send, b"Subject: test\r\n\r\nbody")
        assert error.value.smtp_code == 451 and b"secret" not in error.value.smtp_error
    finally:
        server.close()
        await server.wait_closed()
    settings.smtp_allow_plaintext = False
    with pytest.raises(RuntimeError, match="SMTP_TLS_REQUIRED"):
        tls_context(settings)


def test_concurrent_idempotency_and_team_isolation(ingest):
    factory, store, settings, _, token = ingest
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks required")

    def send(_):
        return receive(
            factory,
            store,
            b"payload",
            "text/plain",
            kind="REST",
            token_hash=digest(token),
            idempotency_key="same",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(send, range(16)))
    assert len(set(ids)) == 1
    with factory.begin() as db:
        other_team = uuid4()
        db.add(Team(id=other_team, name="Другой контур"))
        db.flush()
        _, other_token = issue_key(db, "Другой ключ", team_id=other_team)
    other = receive(
        factory,
        store,
        b"payload",
        "text/plain",
        kind="REST",
        token_hash=digest(other_token),
        idempotency_key="same",
    )
    assert other != ids[0]
    process(ingest)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 2
        assert (
            db.scalar(
                select(func.count()).select_from(Event).where(Event.team_id == DEFAULT_TEAM_ID)
            )
            == 1
        )


def test_normalization_transaction_failure_rolls_back_and_can_resume(ingest, monkeypatch):
    from app.services import normalization
    from app.workers.queue import complete

    factory, store, settings, _, _ = ingest
    receipt = receive(factory, store, mail(), "message/rfc822", kind="SMTP")

    def fail_after_effect(db, lease, effect):
        complete(db, lease, effect)
        raise RuntimeError("interrupted before commit")

    monkeypatch.setattr(normalization, "complete", fail_after_effect)
    identifiers = process(ingest)
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 0
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0
        assert db.get(RawMessage, receipt).state == "PENDING"
        task = db.get(WorkItem, identifiers[0])
        assert task.state == "PENDING"
        task.due_at = clock(db)
    monkeypatch.setattr(normalization, "complete", complete)
    process(ingest)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        assert db.scalar(select(func.count()).select_from(Attachment)) == 1
        assert db.get(RawMessage, receipt).state == "NORMALIZED"


@pytest.mark.anyio
async def test_raw_and_attachment_access_is_scoped_to_event_and_team(setup, ingest):
    app, factory, password, _, _ = setup
    _, store, _, _, token = ingest
    receive(factory, store, mail(), "message/rfc822", kind="SMTP")
    receive(factory, store, b"other", "text/plain", kind="REST", token_hash=digest(token))
    process(ingest)
    with factory() as db:
        events = db.scalars(select(Event).order_by(Event.input_adapter)).all()
        attachment_id = db.scalar(select(Attachment.id))
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        path = f"/api/v1/events/{events[1].id}/attachments/{attachment_id}"
        assert (await client.get(path)).status_code == 401
        await login(client, password)
        assert (await client.get(path)).content == b"attachment-original"
        assert (
            await client.get(f"/api/v1/events/{events[0].id}/attachments/{attachment_id}")
        ).status_code == 404
        with factory.begin() as db:
            other_team = uuid4()
            db.add(Team(id=other_team, name="Изолированная команда"))
            db.flush()
            db.get(Event, events[1].id).team_id = other_team
        assert (await client.get(path)).status_code == 404
        assert (await client.get(f"/api/v1/events/{events[1].id}")).status_code == 404
        assert len((await client.get("/api/v1/events")).json()["items"]) == 1


def test_ingest_cli_displays_key_once_and_audit_has_no_secret(ingest, monkeypatch, capsys):
    import sys

    from app import ingest_cli
    from app.persistence.models import AuditEntry

    factory, _, _, _, _ = ingest
    monkeypatch.setattr(ingest_cli, "build_engine", lambda _: factory.kw["bind"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["ingest_cli", "create", "--name", "Новый ключ"])
    ingest_cli.main()
    output = capsys.readouterr().out.splitlines()
    identifier, token = output[-2:]
    assert "90 дней" in output[0]
    with factory() as db:
        row = db.scalar(select(IngestCredential).where(IngestCredential.name == "Новый ключ"))
        assert row.token_hash == digest(token) and row.token_hash != token
        audit = db.scalar(select(AuditEntry).where(AuditEntry.action == "INGEST_KEY_CREATED"))
        assert audit.before == {} and audit.after == {} and audit.entity_type == "INGEST_CREDENTIAL"
    monkeypatch.setattr(sys, "argv", ["ingest_cli", "list"])
    ingest_cli.main()
    assert token not in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["ingest_cli", "revoke", "--id", identifier])
    ingest_cli.main()
    with factory() as db:
        assert db.scalar(
            select(IngestCredential).where(IngestCredential.name == "Новый ключ")
        ).revoked_at


@pytest.mark.anyio
async def test_smtp_requires_starttls_and_limits_connections(ingest, tmp_path):
    import ssl
    import subprocess

    factory, store, settings, _, _ = ingest
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    settings.smtp_tls_cert, settings.smtp_tls_key = cert, key
    settings.smtp_max_connections = 1
    connections = set()
    server = await asyncio.get_running_loop().create_server(
        lambda: RestrictedSMTP(
            Receiver(factory, store, settings),
            connections,
            settings,
            tls_context=tls_context(settings),
            require_starttls=True,
        ),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]

    def send():
        with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
            client.ehlo()
            assert client.mail("sender@example.org")[0] == 530
            with pytest.raises(smtplib.SMTPConnectError) as error:
                smtplib.SMTP("127.0.0.1", port, timeout=5)
            assert error.value.smtp_code == 421
            client.starttls(context=ssl.create_default_context(cafile=cert))
            assert (
                client.sendmail(
                    "sender@example.org", ["events@localhost"], b"Subject: TLS\r\n\r\nbody"
                )
                == {}
            )

    try:
        await asyncio.to_thread(send)
        await asyncio.sleep(0.05)
        settings.smtp_allowed_networks = []
        with pytest.raises(smtplib.SMTPConnectError) as error:
            await asyncio.to_thread(smtplib.SMTP, "127.0.0.1", port, timeout=5)
        assert error.value.smtp_code == 421
    finally:
        server.close()
        await server.wait_closed()


def test_attached_message_is_downloadable_and_not_mixed_into_body():
    nested = EmailMessage()
    nested["Subject"] = "Вложенное письмо"
    nested.set_content("Текст вложенного письма")
    outer = EmailMessage()
    outer.set_content("Основной текст")
    outer.add_attachment(nested, filename="forwarded.eml")
    result = parse(outer.as_bytes(policy=policy.SMTP), "message/rfc822")
    assert result.body.strip() == "Основной текст"
    assert len(result.attachments) == 1
    name, kind, payload = result.attachments[0]
    assert name == "forwarded.eml" and kind == "message/rfc822"
    assert parse(payload, kind).subject == "Вложенное письмо"


@pytest.mark.parametrize("interruption", ["revoked", "disabled", "collected", "rollback"])
def test_interrupted_write_cannot_confirm_or_revive_reservation(ingest, monkeypatch, interruption):
    from app.api.errors import ApiFailure
    from app.services import ingestion
    from app.storage.files import StorageError
    from app.storage.service import request_delete

    factory, store, _, key_id, token = ingest
    original = store.write
    reserved = []

    def write(identifier, chunks):
        # A separate connection observes the committed reservation before bytes exist.
        with factory() as db:
            record = db.get(BlobRecord, identifier)
            assert record.state == "STAGING" and record.checksum is None
            assert not store._path(identifier).exists()
        reserved.append(identifier)
        result = original(identifier, chunks)
        with factory.begin() as db:
            if interruption == "revoked":
                db.get(IngestCredential, key_id).revoked_at = clock(db)
            elif interruption == "disabled":
                db.scalar(select(AdapterInstallation)).enabled = False
            elif interruption == "collected":
                request_delete(db, db.get(BlobRecord, identifier))
        return result

    def fail(*args, **kwargs):
        raise RuntimeError("simulated final transaction failure")

    monkeypatch.setattr(store, "write", write)
    if interruption == "rollback":
        monkeypatch.setattr(ingestion, "enqueue", fail)
    error = {
        "revoked": ApiFailure,
        "disabled": ApiFailure,
        "collected": StorageError,
        "rollback": RuntimeError,
    }[interruption]
    with pytest.raises(error):
        receive(factory, store, b"original", "text/plain", kind="REST", token_hash=digest(token))
    assert len(reserved) == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(RawMessage)) == 0
        assert (
            db.scalar(
                select(func.count()).select_from(WorkItem).where(WorkItem.kind == "NORMALIZE")
            )
            == 0
        )
        record = db.get(BlobRecord, reserved[0])
        assert record.state == ("DELETING" if interruption == "collected" else "STAGING")
        assert record.checksum is None and record.size is None and record.owner_id is None


@pytest.mark.parametrize("failure_point", ["file", "directory"])
def test_receive_fsync_failure_never_confirms(ingest, monkeypatch, failure_point):
    import os

    factory, store, _, _, token = ingest

    def fail(_):
        raise OSError("simulated flush failure")

    monkeypatch.setattr(
        os if failure_point == "file" else store,
        "fsync" if failure_point == "file" else "_sync",
        fail,
    )
    with pytest.raises(OSError):
        receive(factory, store, b"original", "text/plain", kind="REST", token_hash=digest(token))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(RawMessage)) == 0
        assert db.scalar(select(func.count()).select_from(WorkItem)) == 0
        record = db.scalar(select(BlobRecord))
        assert record.state == "STAGING" and record.checksum is None


def test_concurrent_rate_limit_is_exact_and_separate_per_key(ingest, monkeypatch):
    from app.api.errors import ApiFailure, ErrorCode
    from app.services import ingestion

    factory, _, _, key_id, token = ingest
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks required")
    with factory.begin() as db:
        now = clock(db)
        other_id, other_token = issue_key(db, "Другой отправитель")
    # Keep a single rate window even if the test starts at the end of a minute.
    monkeypatch.setattr(ingestion, "clock", lambda db: now)

    def consume(value):
        try:
            with factory.begin() as db:
                ingestion.credential(db, digest(value), consume=True, limit=5)
            return True
        except ApiFailure as error:
            assert error.code == ErrorCode.RATE_LIMITED
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume, [token] * 16))
    assert sum(results) == 5
    assert consume(other_token)
    with factory() as db:
        assert db.get(IngestCredential, key_id).attempts == 5
        assert db.get(IngestCredential, other_id).attempts == 1


def test_preflight_team_lock_precedes_credential_and_adapter_locks(ingest):
    import threading

    from sqlalchemy import event, text

    from app.services.ingestion import adapter

    factory, store, _, key_id, token = ingest
    engine = factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL foreign-key and row locks required")
    with factory.begin() as db:
        adapter(db, "REST", DEFAULT_TEAM_ID)
    waiting = threading.Event()

    def before_sql(connection, cursor, statement, parameters, context, executemany):
        sql = " ".join(statement.lower().split())
        if ("from teams" in sql and "for share" in sql) or sql.startswith("insert into blobs"):
            waiting.set()

    event.listen(engine, "before_cursor_execute", before_sql)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with factory.begin() as admin:
                admin.scalar(select(Team).where(Team.id == DEFAULT_TEAM_ID).with_for_update())
                future = pool.submit(
                    receive,
                    factory,
                    store,
                    b"concurrent",
                    "text/plain",
                    kind="REST",
                    token_hash=digest(token),
                )
                assert waiting.wait(5), "receipt did not reach the team barrier"
                admin.execute(text("SET LOCAL lock_timeout = '1s'"))
                # An administrator holding Team must be able to lock these rows:
                # preflight cannot hold them while waiting for its reservation's FK.
                admin.scalar(
                    select(IngestCredential).where(IngestCredential.id == key_id).with_for_update()
                )
                admin.scalar(
                    select(AdapterInstallation)
                    .where(AdapterInstallation.team_id == DEFAULT_TEAM_ID)
                    .with_for_update()
                )
            identifier = future.result(timeout=5)
        with factory() as db:
            raw = db.get(RawMessage, identifier)
            blob = db.get(BlobRecord, raw.blob_id)
            assert blob.state == "READY"
            assert store.read(blob.id, blob.size, blob.checksum) == b"concurrent"
    finally:
        event.remove(engine, "before_cursor_execute", before_sql)
