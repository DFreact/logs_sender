"""One-shot setup of a fresh dedicated database. No passwords in argv or output."""

import json
from pathlib import Path

import psycopg
from psycopg import sql

from app.persistence.privileges import APP_ROLE, MIGRATION_ROLE
from app.settings import Settings


def configure(connection, application_password, migration_password):
    identity = connection.execute(
        "SELECT current_user, current_database(), rolsuper FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if identity != ("eventhub", "eventhub", True):
        raise RuntimeError("DEDICATED_DATABASE_ADMIN_REQUIRED")
    # Never silently convert ownership in an existing legacy/shared deployment.
    if connection.execute(
        "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN ('r','p','S','v','m','f') "
        "AND pg_get_userbyid(c.relowner) <> %s LIMIT 1",
        (MIGRATION_ROLE,),
    ).fetchone():
        raise RuntimeError("EXISTING_DATABASE_REQUIRES_PLANNED_UPGRADE")
    if connection.execute(
        "SELECT 1 FROM pg_database WHERE datname NOT IN "
        "('eventhub','postgres','template0','template1') LIMIT 1"
    ).fetchone():
        raise RuntimeError("DEDICATED_DATABASE_REQUIRED")
    for name, password in ((APP_ROLE, application_password), (MIGRATION_ROLE, migration_password)):
        if len(password) < 32:
            raise ValueError("DATABASE_SECRET_TOO_SHORT")
        if connection.execute(
            "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
            "WHERE r.rolname=%s LIMIT 1",
            (name,),
        ).fetchone():
            raise RuntimeError("UNEXPECTED_ROLE_MEMBERSHIP")
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
            connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(name)))
        # libpq generates SCRAM; plaintext passwords are never placed in SQL statements.
        verifier = connection.pgconn.encrypt_password(
            password.encode(), name.encode(), algorithm=b"scram-sha-256"
        ).decode()
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(name), sql.Literal(verifier))
        )
    for name in ("eventhub", "postgres", "template1"):
        connection.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
        )
    connection.execute("GRANT CONNECT ON DATABASE eventhub TO eventhub_app, eventhub_migrator")
    # Trusted pg_trgm is installed by migrations, without superuser rights.
    connection.execute("GRANT CREATE ON DATABASE eventhub TO eventhub_migrator")
    connection.execute("ALTER SCHEMA public OWNER TO eventhub_migrator")
    connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    connection.execute("GRANT USAGE ON SCHEMA public TO eventhub_app")
    connection.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE eventhub_migrator IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eventhub_app"
    )
    connection.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE eventhub_migrator IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO eventhub_app"
    )
    connection.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE eventhub_migrator "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )
    connection.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE eventhub_migrator "
        "GRANT EXECUTE ON FUNCTIONS TO eventhub_app"
    )


def main():
    settings = Settings()
    url = settings.database_url()
    with psycopg.connect(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
        connect_timeout=5,
    ) as connection:
        configure(
            connection,
            Path("/run/secrets/db_password").read_text().strip(),
            Path("/run/secrets/db_migration_password").read_text().strip(),
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"code": "DATABASE_ROLE_SETUP_FAILED"}))
        raise SystemExit(1) from None
