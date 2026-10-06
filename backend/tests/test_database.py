from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

from app.persistence.database import Base, build_engine, build_session_factory
from app.settings import Settings


def test_database_configuration_has_no_default_secret(monkeypatch):
    monkeypatch.delenv("HUB_DB_PASSWORD", raising=False)
    monkeypatch.delenv("HUB_DB_PASSWORD_FILE", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_SECRET_REQUIRED"):
        Settings().database_url()


def test_url_keeps_special_characters_and_hides_password(tmp_path):
    secret = tmp_path / "password"
    secret.write_text("p@ss:%/word\n")
    settings = Settings(db_password_file=secret)
    assert settings.database_url().password == "p@ss:%/word"
    assert "p@ss" not in str(settings.database_url())
    engine = build_engine(settings)
    assert engine.hide_parameters
    assert build_session_factory(engine).kw["bind"] is engine
    engine.dispose()


def test_source_migration_preserves_existing_event_and_occurrence(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    now = datetime.now(UTC)
    team_id, adapter_id, blob_id, raw_id, event_id, occurrence_id = [uuid4().hex for _ in range(6)]
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0004")
        metadata = MetaData()

        def table(name):
            return Table(name, metadata, autoload_with=connection)

        connection.execute(table("teams").insert().values(id=team_id, name="Проверка миграции"))
        connection.execute(
            table("adapter_installations")
            .insert()
            .values(
                id=adapter_id,
                team_id=team_id,
                kind="SMTP",
                enabled=True,
                visible=True,
            )
        )
        connection.execute(
            table("blobs")
            .insert()
            .values(
                id=blob_id,
                team_id=team_id,
                state="READY",
                checksum="a" * 64,
                size=123,
                created_at=now,
                owner_id=raw_id,
                owner_kind="RAW_MESSAGE",
            )
        )
        connection.execute(
            table("raw_messages")
            .insert()
            .values(
                id=raw_id,
                team_id=team_id,
                adapter_id=adapter_id,
                payload_hash="a" * 64,
                blob_id=blob_id,
                content_type="message/rfc822",
                received_at=now,
                envelope_sender="backup@example.org",
                recipients=[],
                state="NORMALIZED",
                headers=[],
            )
        )
        connection.execute(
            table("events")
            .insert()
            .values(
                id=event_id,
                team_id=team_id,
                input_adapter="SMTP",
                sender="backup@example.org",
                subject="Резервная копия",
                body="Архив сохранён",
                severity="INFO",
                status="ACKNOWLEDGED",
                received_at=now,
                first_seen_at=now,
                last_seen_at=now,
                occurrence_count=1,
            )
        )
        connection.execute(
            table("event_occurrences")
            .insert()
            .values(
                id=occurrence_id,
                event_id=event_id,
                raw_message_id=raw_id,
                received_at=now,
            )
        )
        command.upgrade(config, "head")
        metadata.clear()
        event = connection.execute(select(table("events"))).mappings().one()
        occurrence = connection.execute(select(table("event_occurrences"))).mappings().one()
        assert event["status"] == "ACKNOWLEDGED" and event["occurrence_count"] == 1
        assert event["source_id"] is None and event["category"] == "" and event["tags"] == []
        assert occurrence["normalized_snapshot"] == {
            "sender": "backup@example.org",
            "subject": "Резервная копия",
            "body": "Архив сохранён",
            "severity": "INFO",
            "category": "",
            "event_type": "",
            "tags": [],
        }
        assert occurrence["raw_message_id"] == raw_id
        assert occurrence["rule_version_id"] is None
        command.downgrade(config, "0004")
        assert connection.scalar(text("SELECT count(*) FROM events")) == 1
        assert connection.scalar(text("SELECT count(*) FROM event_occurrences")) == 1
    engine.dispose()


def test_baseline_and_identity_migrations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001_bootstrap")
        assert inspect(connection).get_table_names() == ["alembic_version"]
        version = connection.scalar(text("select version_num from alembic_version"))
        assert version == "0001_bootstrap"
        command.upgrade(config, "head")
        command.check(config)
        assert set(inspect(connection).get_table_names()) == {
            "alembic_version",
            "teams",
            "users",
            "memberships",
            "sessions",
            "audit_entries",
            "login_limits",
            "work_items",
            "blobs",
            "storage_scans",
            "component_heartbeats",
            "adapter_installations",
            "ingest_credentials",
            "raw_messages",
            "events",
            "event_occurrences",
            "attachments",
            "sources",
            "identification_rules",
            "rule_versions",
            "dedup_policies",
            "dedup_keys",
            "routing_rules",
            "rule_executions",
            "output_adapters",
            "notification_channels",
            "templates",
            "template_versions",
            "notifications",
            "delivery_attempts",
            "notification_operations",
            "escalation_policies",
            "escalation_policy_versions",
            "escalation_runs",
            "escalation_steps",
            "event_actions",
            "digest_definitions",
            "digest_versions",
            "digest_receipts",
            "digest_runs",
            "digest_items",
            "retention_policies",
            "retention_progress",
            "metric_counters",
            "health_samples",
        }
        assert connection.scalar(text("SELECT count(*) FROM users")) == 0
        command.downgrade(config, "base")
        assert connection.scalar(text("select count(*) from alembic_version")) == 0
    assert set(Base.metadata.tables) == {
        "teams",
        "users",
        "memberships",
        "sessions",
        "audit_entries",
        "login_limits",
        "work_items",
        "blobs",
        "storage_scans",
        "component_heartbeats",
        "adapter_installations",
        "ingest_credentials",
        "raw_messages",
        "events",
        "event_occurrences",
        "attachments",
        "sources",
        "identification_rules",
        "rule_versions",
        "dedup_policies",
        "dedup_keys",
        "routing_rules",
        "rule_executions",
        "output_adapters",
        "notification_channels",
        "templates",
        "template_versions",
        "notifications",
        "delivery_attempts",
        "notification_operations",
        "escalation_policies",
        "escalation_policy_versions",
        "escalation_runs",
        "escalation_steps",
        "event_actions",
        "digest_definitions",
        "digest_versions",
        "digest_receipts",
        "digest_runs",
        "digest_items",
        "retention_policies",
        "retention_progress",
        "metric_counters",
        "health_samples",
    }
    engine.dispose()


def test_routing_migration_preserves_identification_versions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'versions.db'}")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    team_id, user_id, source_id, rule_id, version_id = [uuid4().hex for _ in range(5)]
    now = datetime.now(UTC)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005")
        metadata = MetaData()

        def insert(table_name, **values):
            connection.execute(
                Table(table_name, metadata, autoload_with=connection).insert().values(**values)
            )

        insert("teams", id=team_id, name="Проверка версий")
        insert(
            "users",
            id=user_id,
            username="version-check",
            display_name="Проверка",
            password_hash="unused-in-migration-test",
            active=False,
            version=1,
            created_at=now,
        )
        insert(
            "sources",
            id=source_id,
            team_id=team_id,
            name="Источник",
            description="",
            enabled=True,
            version=1,
        )
        insert(
            "identification_rules",
            id=rule_id,
            team_id=team_id,
            source_id=source_id,
            name="Правило",
            priority=10,
            enabled=True,
            version=1,
            conditions={"field": "sender", "operator": "exists"},
            assignments={},
        )
        insert(
            "rule_versions",
            id=version_id,
            rule_id=rule_id,
            version=1,
            actor_id=user_id,
            created_at=now,
            snapshot={"name": "Исходная версия"},
        )
        command.upgrade(config, "head")
        versions = Table("rule_versions", MetaData(), autoload_with=connection)
        old = connection.execute(select(versions)).mappings().one()
        assert old["id"] == version_id and old["rule_id"] == rule_id
        assert old["routing_rule_id"] is None and old["snapshot"] == {"name": "Исходная версия"}
        command.check(config)
        command.downgrade(config, "0005")
        versions = Table("rule_versions", MetaData(), autoload_with=connection)
        old = connection.execute(select(versions)).mappings().one()
        assert old["rule_id"] == rule_id and old["actor_id"] == user_id
    engine.dispose()
