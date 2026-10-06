"""Privileges for a dedicated EventHub database; never used by request handlers."""

from sqlalchemy import text

APP_ROLE = "eventhub_app"
MIGRATION_ROLE = "eventhub_migrator"


def grant_runtime(connection):
    if connection.scalar(text("SELECT current_user")) != MIGRATION_ROLE:
        raise RuntimeError("MIGRATION_ROLE_REQUIRED")
    # pg_restore --clean can recreate public and remove schema-scoped defaults.
    connection.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE "
            "ON TABLES TO eventhub_app"
        )
    )
    connection.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT "
            "ON SEQUENCES TO eventhub_app"
        )
    )
    connection.execute(text("GRANT USAGE ON SCHEMA public TO eventhub_app"))
    connection.execute(
        text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eventhub_app")
    )
    connection.execute(
        text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eventhub_app")
    )
    # Runtime can observe the revision, but cannot change migration history.
    connection.execute(text("REVOKE ALL ON public.alembic_version FROM eventhub_app"))
    connection.execute(text("GRANT SELECT ON public.alembic_version TO eventhub_app"))
