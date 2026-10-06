from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.api import (
    appearance,
    audit,
    auth,
    channels,
    configuration,
    connections,
    escalations,
    event_actions,
    events,
    health,
    ingest,
    notifications,
    retention,
    routing,
    users,
)
from app.api.errors import (
    HTTP_ERROR_CODES,
    ApiFailure,
    ErrorCode,
    FieldError,
    error_response,
    log_failure,
)
from app.settings import Settings


class LivenessResponse(BaseModel):
    status: Literal["HEALTHY"] = "HEALTHY"
    checked_at: datetime


def create_app(settings: Settings | None = None, session_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        yield
        if application.state.engine is not None:
            application.state.engine.dispose()

    # No externally hosted documentation assets or debug pages in the MVP UI.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, debug=False, lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.session_factory = session_factory
    app.state.engine = None
    app.state.database_lock = Lock()
    from app.api.digests import router as digests_router

    app.include_router(digests_router)
    app.include_router(appearance.router)
    app.include_router(retention.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(audit.router)
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(connections.router)
    app.include_router(events.router)
    app.include_router(event_actions.router)
    app.include_router(escalations.router)
    app.include_router(configuration.router)
    app.include_router(routing.router)
    app.include_router(channels.router)
    app.include_router(notifications.router)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        # Never echo an untrusted incoming request ID.
        request.state.request_id = uuid4().hex
        try:
            response = await call_next(request)
        except SQLAlchemyError as exc:
            log_failure(exc, request.state.request_id)
            response = error_response(request, 503, ErrorCode.SERVICE_UNAVAILABLE)
        except Exception as exc:
            log_failure(exc, request.state.request_id)
            response = error_response(request, 500, ErrorCode.INTERNAL_ERROR)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ApiFailure)
    async def expected_error(request: Request, exc: ApiFailure):
        return error_response(request, exc.status, exc.code, exc.fields)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(
            request,
            exc.status_code,
            HTTP_ERROR_CODES.get(exc.status_code, ErrorCode.INTERNAL_ERROR),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        allowed = {
            "username",
            "display_name",
            "password",
            "role",
            "active",
            "current_password",
            "new_password",
        }
        fields = []
        for error in exc.errors():
            loc = error.get("loc", ())
            if len(loc) == 2 and loc[0] == "body" and loc[1] in allowed:
                code = (
                    ErrorCode.VALIDATION_REQUIRED
                    if error["type"] == "missing"
                    else (
                        ErrorCode.PASSWORD_POLICY
                        if loc[1] in {"password", "new_password"}
                        else ErrorCode.VALIDATION_ERROR
                    )
                )
                fields.append(FieldError(field=loc[1], code=code))
        return error_response(request, 422, ErrorCode.VALIDATION_ERROR, fields)

    @app.get("/health/live", response_model=LivenessResponse)
    async def liveness():
        # Deliberately independent of database connectivity; readiness is a later stage.
        return LivenessResponse(checked_at=datetime.now(UTC))

    return app


app = create_app()
