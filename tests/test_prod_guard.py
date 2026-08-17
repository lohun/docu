import pytest

from app.config import Settings, validate_settings


def test_dev_settings_pass_validation() -> None:
    settings = Settings(_env_file=None, environment="development")
    validate_settings(settings)  # should not raise


def test_production_default_secret_is_rejected() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key="dev-only-insecure-secret-change-me",
    )
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        validate_settings(settings)


def test_production_short_secret_is_rejected() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key="too-short",
    )
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        validate_settings(settings)


def test_production_requires_secure_cookies() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key="a-strong-production-secret-that-is-long-enough",
        cookie_secure=False,
    )
    with pytest.raises(RuntimeError, match="Secure cookies"):
        validate_settings(settings)


def test_production_cloudinary_backend_requires_url() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key="a-strong-production-secret-that-is-long-enough",
        storage_backend="cloudinary",
        cloudinary_url="",
    )
    with pytest.raises(RuntimeError, match="CLOUDINARY_URL"):
        validate_settings(settings)

    settings.cloudinary_url = "cloudinary://key:secret@mycloud"
    validate_settings(settings)  # should not raise


def test_production_valid_config_passes() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key="a-strong-production-secret-that-is-long-enough",
    )
    validate_settings(settings)


def test_non_production_ignores_weak_secret() -> None:
    settings = Settings(_env_file=None, environment="staging", jwt_secret_key="weak")
    validate_settings(settings)  # staging is not production: no hard fail


def test_production_app_refuses_to_boot_with_default_secret(monkeypatch) -> None:
    monkeypatch.setenv("DOCVERSION_ENVIRONMENT", "production")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        from app.main import create_app

        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            create_app()
    finally:
        get_settings.cache_clear()