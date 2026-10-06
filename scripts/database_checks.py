"""Exercise actual runtime database privileges on a disposable installation."""

import json

PROBE = r"""
import json
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.persistence.database import build_engine
from app.settings import Settings
engine = build_engine(Settings())
checks = {}
with engine.connect() as connection:
    role = connection.execute(text("SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
    assert tuple(role) == ('eventhub_app', False, False, False, False, False)
    assert connection.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','S') AND pg_get_userbyid(c.relowner)='eventhub_app'")) == 0
    assert connection.scalar(text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.users'::regclass")) == 'eventhub_migrator'
    assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == '0012'
    # Even an empty write to Alembic's table must be denied.
    attempts = {
        'create_table': 'CREATE TABLE public.runtime_permission_probe(id integer)',
        'temporary_table': 'CREATE TEMP TABLE runtime_permission_probe(id integer)',
        'alter_table': 'ALTER TABLE public.users ADD COLUMN runtime_permission_probe integer',
        'truncate': 'TRUNCATE public.audit_entries',
        'migration_history': "UPDATE public.alembic_version SET version_num=version_num WHERE false",
        'create_role': 'CREATE ROLE runtime_permission_probe',
        'assume_admin': 'SET ROLE eventhub',
        'assume_migrator': 'SET ROLE eventhub_migrator',
        'read_server_file': "SELECT pg_read_file('/etc/passwd')",
        'become_superuser': 'ALTER ROLE eventhub_app SUPERUSER',
    }
    for label, statement in attempts.items():
        try:
            with connection.begin_nested():
                connection.execute(text(statement))
                raise AssertionError('UNEXPECTED_PRIVILEGE:' + label)
        except DBAPIError as error:
            assert error.orig.sqlstate == '42501', (label, error.orig.sqlstate)
            checks[label] = 'denied'
    # Existing and newly added application tables receive DML through migrations.
    assert connection.scalar(text("SELECT has_table_privilege(current_user, 'public.events', 'SELECT, INSERT, UPDATE, DELETE')"))
    assert not connection.scalar(text("SELECT has_database_privilege(current_user, 'postgres', 'CONNECT')"))
    checks['runtime_role'] = 'eventhub_app'
    checks['schema_owner'] = 'eventhub_migrator'
    checks['schema_revision'] = '0012'
engine.dispose()
print(json.dumps(checks))
"""

EXPECTED_SECRETS = {
    "api": {"db_password", "redis_password", "auth_secret", "channel_secret"},
    "delivery": {"db_password", "redis_password", "channel_secret"},
    "worker": {"db_password", "redis_password"},
    "ingestion": {"db_password", "redis_password"},
    "dispatcher": {"db_password", "redis_password"},
    "scheduler": {"db_password"},
    "smtp": {"db_password"},
    "postgres": {"db_admin_password"},
    "redis": {"redis_password"},
    "web": set(),
}


def verify(run):
    config = json.loads(run("--profile", "maintenance", "config", "--format", "json"))
    all_secrets = {
        **EXPECTED_SECRETS,
        "db-init": {"db_admin_password", "db_password", "db_migration_password"},
        "migrate": {"db_migration_password"},
        "db-restore": {"db_migration_password"},
        "storage-init": set(),
    }
    for name, expected in all_secrets.items():
        assert {
            item["source"] for item in config["services"][name].get("secrets", [])
        } == expected, name
    checks = json.loads(run("exec", "-T", "api", "python", "-", input_text=PROBE))
    for service, expected in EXPECTED_SECRETS.items():
        result = run(
            "exec",
            "-T",
            service,
            "sh",
            "-c",
            'if [ -d /run/secrets ]; then for path in /run/secrets/*; do [ ! -f "$path" ] || basename "$path"; done; fi',
        )
        assert set(result.split()) == expected, service
    print(
        "Права PostgreSQL и секреты: приложение не может изменять схему, читать файлы сервера или повышать права.",
        flush=True,
    )
    checks["future_tables_and_sequences"] = verify_future_grants(run)
    checks["unsafe_initializer_rejected"] = verify_initializer_boundaries(run)
    return {
        "database": checks,
        "secret_mounts": {k: sorted(v) for k, v in EXPECTED_SECRETS.items()},
    }


def verify_future_grants(run):
    prefix = "from sqlalchemy import text\nfrom app.persistence.database import build_engine\nfrom app.settings import Settings\nengine=build_engine(Settings())\n"

    def migrate(code):
        return run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--pull",
            "never",
            "migrate",
            "python",
            "-",
            input_text=prefix + code,
        )

    migrate(
        "with engine.begin() as c:\n    assert c.scalar(text('SELECT current_user')) == 'eventhub_migrator'\n    c.execute(text('CREATE TABLE public.permission_probe(id bigserial PRIMARY KEY, value text NOT NULL)'))\n"
    )
    try:
        run(
            "exec",
            "-T",
            "api",
            "python",
            "-",
            input_text=prefix
            + """
with engine.begin() as c:
    identifier=c.scalar(text("INSERT INTO public.permission_probe(value) VALUES ('check') RETURNING id"))
    assert c.scalar(text('SELECT value FROM public.permission_probe WHERE id=:id'), {'id':identifier}) == 'check'
    c.execute(text("UPDATE public.permission_probe SET value='changed' WHERE id=:id"), {'id':identifier})
    c.execute(text('DELETE FROM public.permission_probe WHERE id=:id'), {'id':identifier})
""",
        )
    finally:
        migrate(
            "with engine.begin() as c:\n    c.execute(text('DROP TABLE public.permission_probe'))\n"
        )
    return True


def verify_initializer_boundaries(run):
    code = r"""
import psycopg
from app.settings import Settings
from app.persistence.bootstrap import configure
url=Settings().database_url()
with psycopg.connect(host=url.host, port=url.port, dbname=url.database, user=url.username, password=url.password) as c:
    for statement, expected in [
        ('CREATE TABLE public.legacy_owner_probe(id integer)', 'EXISTING_DATABASE_REQUIRES_PLANNED_UPGRADE'),
        ('GRANT pg_read_server_files TO eventhub_app', 'UNEXPECTED_ROLE_MEMBERSHIP'),
    ]:
        try:
            c.execute(statement)
            try:
                configure(c, 'x'*48, 'y'*48)
            except RuntimeError as error:
                assert str(error) == expected
            else:
                raise AssertionError('UNSAFE_DATABASE_ACCEPTED')
        finally:
            c.rollback()
"""
    run(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--pull",
        "never",
        "db-init",
        "python",
        "-",
        input_text=code,
    )
    return True
