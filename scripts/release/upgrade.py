"""Explicit 0011 -> 0012 copy upgrade. Never modify the source installation."""

import sys
from pathlib import Path

import admin


def fingerprint(execute):
    tables = execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
    ).splitlines()
    result = {}
    for name in tables:
        if name == "alembic_version":
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        # Constant-size aggregates; no row contents or credentials leave PostgreSQL.
        row = execute(
            "SELECT count(*), coalesce(sum(('x'||substr(md5(row_to_json(t)::text),1,16))"
            "::bit(64)::bigint),0), coalesce(sum(('x'||substr(md5(row_to_json(t)::text),17,16))"
            "::bit(64)::bigint),0) FROM public." + quoted + " t;"
        )
        result[name] = row.strip().split("|")
    return result


def sql(directory, statement):
    return (
        admin.compose(
            directory,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "eventhub",
            "-d",
            "eventhub",
            "-c",
            statement,
        )
        .decode()
        .strip()
    )


def validate_transition(saved, target):
    if (
        (
            saved.get("release"),
            saved.get("schema"),
            target.get("release"),
            target.get("schema"),
        )
        != ("0.11.0-offline.1", "0011", "0.12.2-offline.1", "0012")
        or not isinstance(saved.get("data_fingerprint"), dict)
        or not saved["data_fingerprint"]
    ):
        raise admin.Failure("damaged")


def restore(args, target):
    saved = admin.verified(args.backup, "backup")
    validate_transition(saved, target)
    admin.validate_archive(args.backup / "blobs.tar.gz")
    directory = admin.prepare(
        args,
        target,
        (args.backup / "channel_secret").read_bytes(),
        (args.backup / "settings.env").read_text(),
    )
    with admin.lock(directory):
        admin.up(directory, "postgres")
        admin.compose(
            directory, "run", "--rm", "--no-deps", "-T", "--pull", "never", "db-init"
        )
        with (args.backup / "database.dump").open("rb") as source:
            admin.compose(
                directory,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--pull",
                "never",
                "db-restore",
                stdin=source,
            )
        if sql(directory, "SELECT version_num FROM alembic_version;") != "0011":
            raise admin.Failure("damaged")
        if (
            fingerprint(lambda query: sql(directory, query))
            != saved["data_fingerprint"]
        ):
            raise admin.Failure("damaged")
        admin.compose(
            directory, "run", "--rm", "--no-deps", "-T", "--pull", "never", "migrate"
        )
        if (
            sql(directory, "SELECT version_num FROM alembic_version;")
            != target["schema"]
        ):
            raise admin.Failure("damaged")
        if (
            fingerprint(lambda query: sql(directory, query))
            != saved["data_fingerprint"]
        ):
            raise admin.Failure("damaged")
        with (args.backup / "blobs.tar.gz").open("rb") as source:
            admin.storage(directory, "restore", stdin=source)
        admin.storage(directory, "check")
        sql(directory, "DELETE FROM sessions;")
        admin.write_json(
            directory / "upgrade-verification.json",
            {
                "source_manifest_sha256": admin.digest(args.backup / "manifest.json"),
                "source_schema": "0011",
                "target_schema": "0012",
                "rows_unchanged_before_and_after_migration": True,
                "file_checksums_and_channel_decryption": True,
                "sessions_revoked": True,
                "tables_checked": len(saved["data_fingerprint"]),
            },
        )
        admin.mark_ready(directory)
    admin.say("restore")


def main():
    parser = admin.Parser(add_help=False)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--smtp-port", type=int, required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--network-policy", type=Path)
    parser.add_argument("--ca-file", type=Path)
    args = parser.parse_args()
    manifest = admin.verified(admin.HERE, "release")
    admin.images(manifest)
    restore(args, manifest)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - CLI boundary must not expose credentials
        admin.say("failed")
        sys.exit(1)
