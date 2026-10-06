from alembic import context

from app.persistence import models  # noqa: F401
from app.persistence.database import Base, build_engine
from app.settings import Settings

target_metadata = Base.metadata


def migrate(connection):
    def include_object(obj, name, type_, reflected, compare_to):
        return not (
            type_ == "index"
            and obj.info.get("postgresql_only")
            and connection.dialect.name != "postgresql"
        )

    context.configure(
        connection=connection, target_metadata=target_metadata, include_object=include_object
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif supplied_connection := context.config.attributes.get("connection"):
    migrate(supplied_connection)
else:
    engine = build_engine(Settings())
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
