from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok() -> None:
    app = create_app()
    with patch("app.main.database_is_ready", AsyncMock(return_value=True)):
        with TestClient(app) as client:
            resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_db_down() -> None:
    app = create_app()
    with patch(
        "app.main.database_is_ready",
        AsyncMock(side_effect=ConnectionError("db unreachable")),
    ):
        with TestClient(app) as client:
            resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "database unavailable"}
