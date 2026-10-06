"""Verify the local Compose stack; never print credentials or raw error bodies."""

import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "-p",
    "eventhub-dev",
    "-f",
    "infra/compose/compose.yaml",
]


def compose(*args, input_text=None):
    result = subprocess.run(
        [*COMPOSE, *args],
        cwd=ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # Container command stderr may include connection details. Keep it out of reports.
        raise RuntimeError(f"COMPOSE_CHECK_FAILED:{args[0]}")
    return result.stdout


def request(base, path, data=None):
    req = urllib.request.Request(base + path, data=data)
    try:
        response = urllib.request.urlopen(req, timeout=12)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read().decode()


def assert_error(base, path, status, code, data=None):
    actual, headers, body = request(base, path, data)
    assert actual == status, (path, actual)
    assert "application/json" in headers["Content-Type"]
    payload = json.loads(body)
    assert payload["code"] == code
    assert payload["params"] == {} and payload["field_errors"] == []
    assert re.fullmatch(r"[a-f0-9]{32}", payload["request_id"])
    assert headers["X-Request-ID"] == payload["request_id"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--exercise-outage", action="store_true")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    status, headers, body = request(base, "/")
    assert status == 200 and 'lang="ru"' in body and "Центр событий" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert not re.search(r'(?:src|href)="https?://', body)
    status, _, body = request(base, "/page-does-not-exist")
    assert status == 404 and 'lang="ru"' in body and "Страница не найдена" in body
    assert "nginx" not in body
    assert request(base, "/health/live")[0] == 200
    ready_status, _, ready_body = request(base, "/health/ready")
    assert ready_status == 200 and json.loads(ready_body) == {"status": "HEALTHY"}
    assert_error(base, "/api/v1/health", 401, "SESSION_EXPIRED")
    assert_error(base, "/metrics", 401, "SESSION_EXPIRED")
    assert_error(base, "/api/missing", 404, "RESOURCE_NOT_FOUND")
    assert_error(
        base, "/api/oversized", 413, "REQUEST_TOO_LARGE", b"x" * (1024 * 1024 + 1)
    )
    print("HTTP, Russian proxy pages, error contracts and security headers: passed")

    for service in (
        "api",
        "web",
        "postgres",
        "redis",
        "worker",
        "ingestion",
        "delivery",
        "smtp",
        "dispatcher",
        "scheduler",
    ):
        assert compose("exec", "-T", service, "id", "-u").strip() != "0"
        identifier = compose("ps", "-q", service).strip()
        log_config = json.loads(
            subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .HostConfig.LogConfig}}",
                    identifier,
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        assert log_config["Type"] == "local"
        assert int(log_config["Config"]["max-file"]) > 0
        assert re.fullmatch(r"[1-9][0-9]*[kmg]?", log_config["Config"]["max-size"])
    compose("exec", "-T", "web", "nginx", "-t")
    print("Non-root processes and Nginx configuration: passed")

    # The entire schema and migration cycle are rolled back, including CREATE SCHEMA.
    # No existing application tables or migration version are changed.
    code = """
from uuid import uuid4
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from app.persistence.database import build_engine
from app.settings import Settings
engine = build_engine(Settings())
with engine.connect() as connection:
    transaction = connection.begin()
    try:
        schema = "verify_" + uuid4().hex
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert version == "0012"
        assert set(inspect(connection).get_table_names(schema=schema)) == {
            "alembic_version", "teams", "users", "memberships", "sessions",
            "login_limits", "audit_entries", "work_items", "blobs",
            "storage_scans", "component_heartbeats",
            "adapter_installations", "ingest_credentials", "raw_messages",
            "events", "event_occurrences", "attachments",
            "sources", "identification_rules", "rule_versions", "dedup_policies", "dedup_keys",
            "routing_rules", "rule_executions", "output_adapters",
            "notification_channels", "templates", "template_versions",
            "notifications", "delivery_attempts", "notification_operations",
            "escalation_policies", "escalation_policy_versions", "escalation_runs",
            "escalation_steps", "event_actions",
            "digest_definitions", "digest_versions", "digest_receipts",
            "digest_runs", "digest_items",
            "retention_policies", "retention_progress", "metric_counters", "health_samples",
        }
        command.check(config)
        command.downgrade(config, "base")
        assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0
    finally:
        transaction.rollback()
engine.dispose()
"""
    compose("exec", "-T", "api", "python", "-", input_text=code)
    print("PostgreSQL migration upgrade/check/downgrade in isolated schema: passed")

    if args.exercise_outage:
        try:
            compose("stop", "api")
            assert_error(base, "/health/live", 503, "SERVICE_UNAVAILABLE")
            assert request(base, "/")[0] == 200
            print("API outage: safe proxy error and accessible shell: passed")
        finally:
            compose("start", "api")
        for _ in range(20):
            if request(base, "/health/live")[0] == 200:
                print("API recovery: passed")
                break
            time.sleep(1)
        else:
            raise RuntimeError("API_RECOVERY_FAILED")


if __name__ == "__main__":
    main()
