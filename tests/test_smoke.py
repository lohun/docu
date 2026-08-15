from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_app_metadata() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "docversion"
    # environment deliberately not exposed on the root route
    assert "environment" not in data


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_name == "docversion"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.log_level == "INFO"
