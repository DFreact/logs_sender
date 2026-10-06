"""Disposable TLS destination, real ingestion/worker/Redis and safe fault checks."""

import ipaddress
import json
import secrets
import smtplib
import subprocess
import time
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fixtures(directory, subnet):
    directory = Path(directory)
    address = str(ipaddress.ip_network(subnet)[250])
    source = """
from datetime import UTC, datetime, timedelta
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local delivery verification")])
cert = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
    .not_valid_after(datetime.now(UTC) + timedelta(days=1))
    .add_extension(
        x509.SubjectAlternativeName([x509.DNSName("api.telegram.org"), x509.DNSName("localhost")]),
        False,
    )
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
    .sign(key, hashes.SHA256())
)
(directory / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(directory / "key.pem").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
)
for p in (directory / "cert.pem", directory / "key.pem"):
    p.chmod(0o444)
"""
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-"],
        input="from pathlib import Path\ndirectory = Path(" + repr(str(directory)) + ")\n" + source,
        text=True,
        check=True,
    )
    environment = f'''      HUB_OUTBOUND_HOSTS: '["api.telegram.org"]'
      HUB_OUTBOUND_NETWORKS: '["{address}/32"]'
      HUB_OUTBOUND_PORTS: '[443,465]'
      HUB_OUTBOUND_CA_FILE: /run/fixtures/cert.pem
      HUB_TELEGRAM_ALLOWED: "true"
      HUB_DELIVERY_RETRY_DELAYS: '[1,2]'
      HUB_DELIVERY_JITTER_PERCENT: "0"
'''
    mounts = f'''    extra_hosts: ["api.telegram.org:{address}"]
    volumes:
      - "{directory}/cert.pem:/run/fixtures/cert.pem:ro"
'''
    return (
        environment
        + mounts
        + "  delivery:\n    environment:\n"
        + environment
        + mounts
        + f'''  ingestion:
    environment:
      HUB_DELIVERY_RETRY_DELAYS: '[1,2]'
      HUB_DELIVERY_JITTER_PERCENT: "0"
  delivery-mock:
    image: eventhub-backend:0.12.2
    command: [python, /run/fixtures/mock.py]
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    volumes:
      - "{directory}/cert.pem:/run/fixtures/cert.pem:ro"
      - "{directory}/key.pem:/run/fixtures/key.pem:ro"
      - "{ROOT}/scripts/delivery_mock.py:/run/fixtures/mock.py:ro"
    networks:
      default:
        ipv4_address: {address}
'''
    )


def verify(run, smtp_port):
    token = "123:" + secrets.token_urlsafe(32)
    seed = """
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import (
    DEFAULT_TEAM_ID,
    User,
    Source,
    IdentificationRule,
    RuleVersion,
    RoutingRule,
    Template,
    TemplateVersion,
    NotificationChannel,
    OutputAdapter,
)
from app.security.channel_secrets import protect
from uuid import uuid4

settings = Settings()
engine = build_engine(settings)
with build_session_factory(engine).begin() as db:
    actor = db.query(User).filter_by(username="admin").one()
    source = Source(
        team_id=DEFAULT_TEAM_ID,
        name="Kaspersky",
        description="Локальная проверка",
        enabled=True,
        version=1,
    )
    db.add(source)
    db.flush()
    conditions = {
        "field": "subject",
        "operator": "contains",
        "value": "delivery-check",
        "case_sensitive": False,
    }
    assignments = {
        "severity": "CRITICAL",
        "category": "Безопасность",
        "event_type": "Обнаружение",
        "tags": [],
    }
    rule = IdentificationRule(
        team_id=DEFAULT_TEAM_ID,
        source_id=source.id,
        name="Проверка доставки",
        enabled=True,
        priority=0,
        version=1,
        conditions=conditions,
        assignments=assignments,
    )
    db.add(rule)
    db.flush()
    db.add(
        RuleVersion(
            rule_id=rule.id,
            version=1,
            actor_id=actor.id,
            snapshot={
                "name": rule.name,
                "source_id": str(source.id),
                "enabled": True,
                "priority": 0,
                "version": 1,
                "conditions": conditions,
                "assignments": assignments,
            },
        )
    )
    template = Template(
        team_id=DEFAULT_TEAM_ID,
        name="Проверка доставки",
        kind="TELEGRAM",
        version=1,
        subject="{{ event.subject }}",
        body="{{ event.source }} — {{ event.severity }}: {{ event.body }}",
        html="",
    )
    db.add(template)
    db.flush()
    db.add(
        TemplateVersion(
            template_id=template.id,
            version=1,
            actor_id=actor.id,
            snapshot={
                "kind": "TELEGRAM",
                "subject": template.subject,
                "body": template.body,
                "html": "",
            },
        )
    )
    channel = NotificationChannel(
        id=uuid4(),
        team_id=DEFAULT_TEAM_ID,
        name="Локальная проверка доставки",
        kind="TELEGRAM",
        template_id=template.id,
        enabled=True,
        configuration={"kind": "TELEGRAM", "chat_id": "-123"},
        secret_version=1,
    )
    channel.secret_ciphertext = protect(settings, DEFAULT_TEAM_ID, channel.id, token)
    db.add(channel)
    db.add(OutputAdapter(team_id=DEFAULT_TEAM_ID, kind="TELEGRAM", enabled=True, visible=False))
    actions = [
        {
            "kind": "NOTIFY",
            "scope": "OCCURRENCE",
            "target_id": str(channel.id),
            "mode": "IMMEDIATE",
            "delay_seconds": None,
            "scheduled_at": None,
        }
    ]
    routing = RoutingRule(
        team_id=DEFAULT_TEAM_ID,
        name="Проверка доставки",
        description="",
        priority=0,
        enabled=True,
        version=1,
        conditions=conditions,
        actions=actions,
    )
    db.add(routing)
    db.flush()
    db.add(
        RuleVersion(
            routing_rule_id=routing.id,
            version=1,
            actor_id=actor.id,
            snapshot={
                "name": routing.name,
                "description": "",
                "priority": 0,
                "enabled": True,
                "version": 1,
                "conditions": conditions,
                "actions": actions,
            },
        )
    )
engine.dispose()
"""
    run("exec", "-T", "api", "python", "-", input_text="token = " + repr(token) + "\n" + seed)

    def mail(marker):
        message = EmailMessage()
        message["From"], message["To"] = "security@example.org", "events@localhost"
        message["Subject"] = "delivery-check " + marker
        message.set_content(marker)
        with smtplib.SMTP("127.0.0.1", smtp_port, timeout=5) as client:
            client.send_message(message)

    def rows():
        raw = run(
            "exec",
            "-T",
            "api",
            "python",
            "-",
            input_text="""
import json
from sqlalchemy import select
from app.settings import Settings
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import Notification, DeliveryAttempt

engine = build_engine(Settings())
with build_session_factory(engine)() as db:
    print(
        json.dumps(
            [
                {
                    "id": str(n.id),
                    "body": n.context["body"],
                    "status": n.status,
                    "uncertain": n.uncertain,
                    "attempts": [
                        a.status
                        for a in db.scalars(
                            select(DeliveryAttempt)
                            .where(DeliveryAttempt.notification_id == n.id)
                            .order_by(DeliveryAttempt.attempt_number)
                        )
                    ],
                }
                for n in db.scalars(select(Notification))
            ]
        )
    )
engine.dispose()
""",
        )
        return json.loads(raw)

    def wait(marker, status, seconds=55):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            found = [r for r in rows() if marker in r["body"] and r["status"] == status]
            if found:
                return found[0]
            time.sleep(0.5)
        raise AssertionError("DELIVERY_STATE_TIMEOUT")

    for marker in ("normal-delivery", "retry-once", "permanent", "uncertain-once"):
        mail(marker)
    assert wait("normal-delivery", "SENT")["attempts"] == ["SENT"]
    assert wait("retry-once", "SENT")["attempts"] == ["FAILED", "SENT"]
    assert wait("permanent", "FAILED")["attempts"] == ["FAILED"]
    unknown = wait("uncertain-once", "SENT")
    assert unknown["uncertain"] and unknown["attempts"] == ["UNKNOWN", "SENT"]
    received = json.loads(
        run(
            "exec",
            "-T",
            "delivery-mock",
            "python",
            "-",
            input_text="""
import urllib.request, ssl

context = ssl.create_default_context(cafile="/run/fixtures/cert.pem")
print(urllib.request.urlopen("https://localhost/state", context=context, timeout=3).read().decode())
""",
        )
    )["messages"]
    assert any("Kaspersky — Критическая:" in m["text"] and m["chat_id"] == "-123" for m in received)
    ambiguous = [m for m in received if "uncertain-once" in m["text"]]
    assert len(ambiguous) == 2 and ambiguous[0]["key"] == ambiguous[1]["key"] == unknown["id"]
    print(
        "SMTP → Kaspersky → Telegram: доставка, 429, постоянный отказ "
        "и неопределённый результат проверены на локальном имитаторе.",
        flush=True,
    )
    run("stop", "redis")
    try:
        mail("redis-outage")
        time.sleep(2)
        assert not any("redis-outage" in r["body"] and r["status"] == "SENT" for r in rows())
    finally:
        run("up", "-d", "--wait", "redis")
    assert wait("redis-outage", "SENT")["attempts"] == ["SENT"]
    print("После восстановления Redis сохранённое сообщение обработано и доставлено.", flush=True)
