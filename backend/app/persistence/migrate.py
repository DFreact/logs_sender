"""Run migrations as their owner, then grant only runtime data access."""

import json
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.persistence.database import build_engine
from app.persistence.privileges import MIGRATION_ROLE, grant_runtime
from app.settings import Settings


def main():
    if sys.argv[1:] not in ([], ["grants"]):
        raise ValueError("INVALID_MIGRATION_ACTION")
    engine = build_engine(Settings())
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_user")) != MIGRATION_ROLE:
                raise RuntimeError("MIGRATION_ROLE_REQUIRED")
        if not sys.argv[1:]:
            command.upgrade(Config("/app/alembic.ini"), "head")
        with engine.begin() as connection:
            grant_runtime(connection)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"code": "DATABASE_MIGRATION_FAILED"}))
        raise SystemExit(1) from None
