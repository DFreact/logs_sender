import re

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.main import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(create_app()), base_url="http://test") as client:
        yield client


async def test_liveness_has_no_database_dependency(client, monkeypatch):
    monkeypatch.delenv("HUB_DB_PASSWORD", raising=False)
    response = await client.get("/health/live", headers={"X-Request-ID": "secret-injected"})
    assert response.status_code == 200
    assert response.json()["status"] == "HEALTHY"
    assert re.fullmatch(r"[a-f0-9]{32}", response.headers["X-Request-ID"])
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["checked_at"].endswith("Z")


@pytest.mark.parametrize("path", ["/missing", "/docs", "/redoc", "/openapi.json"])
async def test_unknown_routes_are_safe(client, path):
    response = await client.get(path)
    assert response.status_code == 404
    assert response.json() == {
        "code": "RESOURCE_NOT_FOUND",
        "params": {},
        "field_errors": [],
        "request_id": response.headers["X-Request-ID"],
    }


async def test_errors_do_not_expose_secrets_or_exception_chains(caplog):
    app = create_app()
    secret = "test-password-token-123"

    @app.get("/crash")
    def crash():
        try:
            raise ValueError(secret)
        except ValueError as cause:
            raise RuntimeError(f"SQL exception SELECT {secret}") from cause

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/crash")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert secret not in response.text + caplog.text
    assert "SQL exception" not in response.text + caplog.text
    assert "frames" in caplog.text
    assert response.headers["X-Request-ID"] in caplog.text


async def test_validation_and_http_details_are_not_passed_through():
    app = create_app()

    class Input(BaseModel):
        count: int

    @app.post("/validate")
    def validate(body: Input):
        return body

    @app.get("/forbidden")
    def forbidden():
        raise HTTPException(403, detail="password=private", headers={"X-Leak": "private"})

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/validate", json={"count": "password=private"})
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert "private" not in response.text
        response = await client.get("/forbidden")
        assert response.json()["code"] == "ACCESS_DENIED"
        assert "private" not in response.text
        assert "X-Leak" not in response.headers
        response = await client.post("/health/live")
        assert response.json()["code"] == "METHOD_NOT_ALLOWED"
