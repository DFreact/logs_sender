import json
import logging
import traceback
from enum import StrEnum
from pathlib import Path

from fastapi import Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

logger = logging.getLogger("eventhub.errors")


class ErrorCode(StrEnum):
    INGEST_KEY_LIMIT = "INGEST_KEY_LIMIT"
    RAW_MESSAGE_EXPIRED = "RAW_MESSAGE_EXPIRED"
    CURSOR_INVALID = "CURSOR_INVALID"
    QUERY_TOO_BROAD = "QUERY_TOO_BROAD"
    DIGEST_BACKLOG = "DIGEST_BACKLOG"
    DIGEST_STATE_CONFLICT = "DIGEST_STATE_CONFLICT"
    EVENT_STATE_CONFLICT = "EVENT_STATE_CONFLICT"
    NOTIFICATION_STATE_CONFLICT = "NOTIFICATION_STATE_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CHANNEL_SECRET_UNAVAILABLE = "CHANNEL_SECRET_UNAVAILABLE"
    OUTBOUND_BLOCKED = "OUTBOUND_BLOCKED"
    CHANNEL_CONNECTION_FAILED = "CHANNEL_CONNECTION_FAILED"
    CHANNEL_UNAVAILABLE = "CHANNEL_UNAVAILABLE"
    ACTION_TARGET_UNAVAILABLE = "ACTION_TARGET_UNAVAILABLE"
    CONFIGURATION_LIMIT = "CONFIGURATION_LIMIT"
    SOURCE_NAME_TAKEN = "SOURCE_NAME_TAKEN"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    INVALID_INPUT = "INVALID_INPUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    ACCESS_DENIED = "ACCESS_DENIED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    RATE_LIMITED = "RATE_LIMITED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    MAIL_CONNECTION_FAILED = "MAIL_CONNECTION_FAILED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    TEMPLATE_INVALID = "TEMPLATE_INVALID"
    CSRF_FAILED = "CSRF_FAILED"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    LAST_ADMINISTRATOR = "LAST_ADMINISTRATOR"
    PASSWORD_POLICY = "PASSWORD_POLICY"
    CURRENT_PASSWORD_INVALID = "CURRENT_PASSWORD_INVALID"


class FieldError(BaseModel):
    field: str
    code: ErrorCode
    params: dict[str, str | int] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    code: ErrorCode
    params: dict[str, str | int] = Field(default_factory=dict)
    field_errors: list[FieldError] = Field(default_factory=list)
    request_id: str


class ApiFailure(Exception):
    def __init__(self, status: int, code: ErrorCode, fields: list[FieldError] | None = None):
        self.status, self.code, self.fields = status, code, fields or []
        super().__init__(code)


def error_response(
    request: Request, status: int, code: ErrorCode, fields: list[FieldError] | None = None
) -> JSONResponse:
    payload = ErrorResponse(
        code=code, request_id=request.state.request_id, field_errors=fields or []
    )
    return JSONResponse(payload.model_dump(), status_code=status)


def log_failure(exc: Exception, request_id: str) -> None:
    # Keep useful stack locations, but no message, source line, local variables,
    # request values or chained exception strings (which can contain credentials).
    frames = [
        {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_tb(exc.__traceback__)[-12:]
    ]
    logger.error(
        json.dumps(
            {
                "code": ErrorCode.INTERNAL_ERROR,
                "request_id": request_id,
                "frames": frames,
            }
        )
    )


HTTP_ERROR_CODES = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.SESSION_EXPIRED,
    403: ErrorCode.ACCESS_DENIED,
    404: ErrorCode.RESOURCE_NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.VERSION_CONFLICT,
    413: ErrorCode.REQUEST_TOO_LARGE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    502: ErrorCode.SERVICE_UNAVAILABLE,
    503: ErrorCode.SERVICE_UNAVAILABLE,
    504: ErrorCode.SERVICE_UNAVAILABLE,
}
