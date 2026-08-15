import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"

_DEFAULT_CORS_ORIGINS = [
    "http://localhost:8080",  # React local development
    "http://localhost:5173",  # Vite local development
    "https://api-monitor-hub.vercel.app",  # Production frontend
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCVERSION_",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = "docversion"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://docversion:docversion@localhost:5432/docversion"
    database_sslmode: str = ""
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_connect_timeout_seconds: int = 10
    jwt_secret_key: str = DEV_JWT_SECRET
    jwt_issuer: str = "docversion-api"
    jwt_audience: str = "docversion-app"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    # When unset, derived from environment: Secure/None outside development so
    # cross-site frontends (Vercel admin, per-org custom domains) can authenticate
    # via httpOnly cookies. Localhost Vite->API is same-site (ports don't matter
    # for SameSite) so Lax works for local development.
    cookie_secure: bool | None = None
    cookie_samesite: str | None = None
    cors_origins: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    mail_from: str = "noreply@example.com"
    frontend_url: str = "http://localhost:5173"
    snapshot_storage_dir: str = "./snapshots"
    git_export_base_dir: str = "./git-exports"
    fernet_master_key: str = ""
    nvidia_api_key: str = ""
    nvidia_api_key_encrypted: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "meta/llama-3.3-70b-instruct"
    llm_call_timeout_seconds: int = 60
    llm_rate_per_minute: int = 10
    scrape_timeout_seconds: int = 30
    DOCVERSION_ENV_FILE: str = ".env"
    DOCVERSION_APP_NAME: str = "docversion"
    DOCVERSION_ENVIRONMENT: str = "development"
    DOCVERSION_DEBUG: bool = False
    DOCVERSION_LOG_LEVEL: str = "INFO"
    NVIDIA_API_KEY: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cookie_secure_enabled(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return not self.is_development

    @property
    def cookie_samesite_value(self) -> str:
        if self.cookie_samesite:
            return self.cookie_samesite
        return "lax" if self.is_development else "none"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins:
            return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return list(_DEFAULT_CORS_ORIGINS)


def validate_settings(settings: Settings) -> None:
    """Fail-fast guard for production boot.

    Refuses to start a production API on obviously insecure configuration so a
    mis-deploy can never silently serve forgeable tokens or plaintext cookies.
    """
    if not settings.is_production:
        return
    if settings.jwt_secret_key == DEV_JWT_SECRET or len(settings.jwt_secret_key) < 32:
        raise RuntimeError(
            "production requires DOCVERSION_JWT_SECRET_KEY to be a strong, "
            "non-default secret (>= 32 characters)"
        )
    if settings.cookie_secure is False:
        raise RuntimeError(
            "production requires Secure cookies (DOCVERSION_COOKIE_SECURE must "
            "not be explicitly false)"
        )


@lru_cache
def get_settings() -> Settings:
    env_file = os.environ.get("DOCVERSION_ENV_FILE", ".env")
    return Settings(_env_file=env_file)