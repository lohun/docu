import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    jwt_secret_key: str = "dev-only-insecure-secret-change-me"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
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

def get_settings() -> Settings:
    env_file = os.environ.get("DOCVERSION_ENV_FILE", ".env")
    return Settings(_env_file=env_file)
