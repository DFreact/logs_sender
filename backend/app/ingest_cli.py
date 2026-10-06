"""Local operator console: short-lived ingest-only keys, never command-line secrets."""

import sys
from uuid import UUID

from sqlalchemy import select

from app.cli import Parser
from app.i18n import t
from app.persistence.database import build_engine, build_session_factory
from app.persistence.models import DEFAULT_TEAM_ID, IngestCredential
from app.security.audit import AuditAction, record
from app.services.ingestion import issue_key
from app.settings import Settings
from app.workers.queue import clock


def main():
    parser = Parser(add_help=False)
    parser.add_argument("command", choices=["create", "list", "revoke"])
    parser.add_argument("--name")
    parser.add_argument("--id", type=UUID)
    args = parser.parse_args()
    if args.command == "create" and (not args.name or len(args.name) > 120):
        raise SystemExit(t("invalidArguments"))
    if args.command == "revoke" and not args.id:
        raise SystemExit(t("invalidArguments"))
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit(t("interactiveRequired"))
    engine = None
    try:
        engine = build_engine(Settings())
        with build_session_factory(engine).begin() as db:
            if args.command == "create":
                identifier, token = issue_key(db, args.name)
                record(
                    db,
                    AuditAction.INGEST_KEY_CREATED,
                    entity_id=identifier,
                    entity_type="INGEST_CREDENTIAL",
                )
            elif args.command == "revoke":
                row = db.scalar(
                    select(IngestCredential)
                    .where(
                        IngestCredential.id == args.id, IngestCredential.team_id == DEFAULT_TEAM_ID
                    )
                    .with_for_update()
                )
                if not row:
                    raise ValueError()
                row.revoked_at = clock(db)
                record(
                    db,
                    AuditAction.INGEST_KEY_REVOKED,
                    entity_id=row.id,
                    entity_type="INGEST_CREDENTIAL",
                )
            else:
                rows = db.scalars(
                    select(IngestCredential)
                    .where(IngestCredential.team_id == DEFAULT_TEAM_ID)
                    .order_by(IngestCredential.created_at)
                ).all()
        if args.command == "create":
            print(t("ingestKeyCreated"))
            print(identifier)
            print(token)
        elif args.command == "revoke":
            print(t("ingestKeyRevoked"))
        else:
            for row in rows:
                print(
                    row.id,
                    row.name,
                    t("ingestKeyInactive") if row.revoked_at else t("ingestKeyExpiry"),
                    row.expires_at.isoformat(),
                )
    except Exception:
        raise SystemExit(t("ingestKeyError")) from None
    finally:
        if engine:
            engine.dispose()


if __name__ == "__main__":
    main()
