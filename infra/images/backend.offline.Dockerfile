ARG DEPENDENCIES=eventhub-backend-deps:0.12.0
FROM ${DEPENDENCIES}
USER root
RUN rm -rf /app/app /app/migrations
COPY backend/requirements.txt /app/requirements.txt
COPY backend/app /app/app
COPY backend/migrations /app/migrations
COPY backend/alembic.ini /app/alembic.ini
RUN python -c 'import importlib.metadata as m; from pathlib import Path; [(lambda name, version: None if m.version(name)==version else (_ for _ in ()).throw(RuntimeError("DEPENDENCIES_MISMATCH")))(*line.split("==")) for line in Path("/app/requirements.txt").read_text().splitlines() if line and not line.startswith("#")]'
USER 10001:10001
