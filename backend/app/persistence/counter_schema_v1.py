"""Transactional, sharded operational totals; no archive scans on health requests.

SQLite and PostgreSQL triggers also cover SQL/Core writes and retention deletes.
Only fixed metric names are stored; never event contents or credentials.
"""

from sqlalchemy import text

STATES = {
    "work_items": ("task", "state", ["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]),
    "notifications": (
        "notification",
        "status",
        ["PENDING", "PROCESSING", "SENT", "FAILED", "RETRYING", "CANCELLED"],
    ),
}
GAUGES = {
    "events": {
        "events": "1",
        "events_new": "CASE WHEN {r}.status = 'NEW' THEN 1 ELSE 0 END",
        "events_critical": "CASE WHEN {r}.severity = 'CRITICAL' THEN 1 ELSE 0 END",
    },
    **{
        table: {
            f"{prefix}_{state}": f"CASE WHEN {{r}}.{column} = '{state}' THEN 1 ELSE 0 END"
            for state in states
        }
        for table, (prefix, column, states) in STATES.items()
    },
}
TOTALS = {"raw_messages": "accepted_total", "event_occurrences": "normalized_total"}
KINDS = sorted(
    [kind for values in GAUGES.values() for kind in values] + list(TOTALS.values()) + ["sent_total"]
)
SHARDS = 16


def union(values, name):
    return " UNION ALL ".join(f"SELECT {value} AS {name}" for value in values)


def seed_sql(team):
    names = union([f"'{name}'" for name in KINDS], "kind")
    shards = union(range(SHARDS), "bucket")
    return (
        "INSERT INTO metric_counters (team_id, kind, bucket, value) "
        f"SELECT {team}, kinds.kind, shards.bucket, 0 FROM ({names}) AS kinds "
        f"CROSS JOIN ({shards}) AS shards WHERE 1=1 "
        "ON CONFLICT (team_id, kind, bucket) DO NOTHING"
    )


def bucket(dialect, row="NEW"):
    return (
        f"get_byte(uuid_send({row}.id), 0) / 16"
        if dialect == "postgresql"
        else f"instr('0123456789abcdef', lower(substr({row}.id, 1, 1))) - 1"
    )


def write_delta(dialect, table, operation, values):
    row = "OLD" if operation == "DELETE" else "NEW"
    team = (
        f"(SELECT team_id FROM events WHERE id = {row}.event_id)"
        if table == "event_occurrences"
        else f"{row}.team_id"
    )
    rows = " UNION ALL ".join(
        f"SELECT '{kind}' AS kind, ({value}) AS delta" for kind, value in sorted(values.items())
    )
    return (
        "INSERT INTO metric_counters (team_id, kind, bucket, value) "
        f"SELECT {team}, changes.kind, {bucket(dialect, row)}, changes.delta "
        f"FROM ({rows}) AS changes WHERE changes.delta <> 0 ORDER BY changes.kind "
        "ON CONFLICT (team_id, kind, bucket) DO UPDATE "
        "SET value = metric_counters.value + excluded.value"
    )


def trigger(connection, table, operation, statement):
    name = f"hub_count_{table}_{operation.lower()}"
    if connection.dialect.name == "postgresql":
        result = "OLD" if operation == "DELETE" else "NEW"
        connection.execute(
            text(f"""CREATE OR REPLACE FUNCTION {name}() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN {statement}; RETURN {result}; END $$""")
        )
        connection.execute(
            text(
                f"CREATE TRIGGER {name} AFTER {operation} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {name}()"
            )
        )
    else:
        connection.execute(
            text(f"CREATE TRIGGER {name} AFTER {operation} ON {table} BEGIN {statement}; END")
        )


def install(connection, *, seed=False):
    dialect = connection.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        return
    if seed:
        # Migration holds write locks; triggers are installed before releasing them.
        teams = connection.execute(text("SELECT id FROM teams")).scalars().all()
        for team in teams:
            connection.execute(text(seed_sql(":team")), {"team": team})
        for table in [*GAUGES, *TOTALS]:
            values = GAUGES.get(table, {TOTALS.get(table): "1"})
            if table == "notifications":
                values = {**values, "sent_total": "CASE WHEN {r}.status = 'SENT' THEN 1 ELSE 0 END"}
            team = "e.team_id" if table == "event_occurrences" else "r.team_id"
            join = " JOIN events e ON e.id = r.event_id" if table == "event_occurrences" else ""
            for kind, expression in values.items():
                connection.execute(
                    text(
                        "INSERT INTO metric_counters(team_id,kind,bucket,value) "
                        f"SELECT {team}, :kind, {bucket(dialect, 'r')}, "
                        f"SUM({expression.format(r='r')}) "
                        f"FROM {table} r{join} GROUP BY {team}, {bucket(dialect, 'r')} "
                        "ON CONFLICT(team_id,kind,bucket) DO UPDATE SET value=excluded.value"
                    ),
                    {"kind": kind},
                )
    trigger(connection, "teams", "INSERT", seed_sql("NEW.id"))
    for table, values in GAUGES.items():
        for operation in ("INSERT", "UPDATE", "DELETE"):
            expressions = {
                key: (
                    expression.format(r="NEW")
                    if operation == "INSERT"
                    else "- (" + expression.format(r="OLD") + ")"
                    if operation == "DELETE"
                    else "("
                    + expression.format(r="NEW")
                    + ") - ("
                    + expression.format(r="OLD")
                    + ")"
                )
                for key, expression in values.items()
            }
            if table == "notifications" and operation != "DELETE":
                expressions["sent_total"] = (
                    "CASE WHEN NEW.status = 'SENT'"
                    + (" AND OLD.status <> 'SENT'" if operation == "UPDATE" else "")
                    + " THEN 1 ELSE 0 END"
                )
            trigger(
                connection, table, operation, write_delta(dialect, table, operation, expressions)
            )
    for table, kind in TOTALS.items():
        trigger(connection, table, "INSERT", write_delta(dialect, table, "INSERT", {kind: "1"}))


def uninstall(connection):
    for table, operations in [
        ("teams", ["INSERT"]),
        *[(table, ["INSERT", "UPDATE", "DELETE"]) for table in GAUGES],
        *[(table, ["INSERT"]) for table in TOTALS],
    ]:
        for operation in operations:
            name = f"hub_count_{table}_{operation.lower()}"
            suffix = f" ON {table}" if connection.dialect.name == "postgresql" else ""
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name}{suffix}"))
            if connection.dialect.name == "postgresql":
                connection.execute(text(f"DROP FUNCTION IF EXISTS {name}()"))


def after_create(metadata, connection, **kwargs):
    install(connection)
