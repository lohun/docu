import pytest

from app.db_urls import async_to_sync, with_sslmode


def test_async_to_sync_swaps_driver_only() -> None:
    url = "postgresql+asyncpg://user:pass@host:6543/dbname"
    assert async_to_sync(url) == "postgresql+psycopg2://user:pass@host:6543/dbname"


def test_async_to_sync_preserves_query_params() -> None:
    url = "postgresql+asyncpg://user:pass@host:6543/dbname?sslmode=require"
    result = async_to_sync(url)
    assert result.startswith("postgresql+psycopg2://")
    assert result.endswith("?sslmode=require")


def test_async_to_sync_idempotent_for_sync_scheme() -> None:
    url = "postgresql+psycopg2://user:pass@host/db"
    assert async_to_sync(url) == url


def test_async_to_sync_rejects_unknown_scheme() -> None:
    with pytest.raises(ValueError):
        async_to_sync("mysql://user:pass@host/db")


def test_with_sslmode_empty_leaves_url_untouched() -> None:
    url = "postgresql+asyncpg://u:p@h:5432/db"
    assert with_sslmode(url, "") == url


def test_with_sslmode_adds_param() -> None:
    url = "postgresql+asyncpg://u:p@h:5432/db"
    assert with_sslmode(url, "require") == "postgresql+asyncpg://u:p@h:5432/db?sslmode=require"


def test_with_sslmode_replaces_existing_value() -> None:
    url = "postgresql+asyncpg://u:p@h:5432/db?sslmode=disable"
    result = with_sslmode(url, "require")
    assert "sslmode=require" in result
    assert "sslmode=disable" not in result


def test_with_sslmode_preserves_other_params() -> None:
    url = "postgresql+asyncpg://u:p@h:5432/db?application_name=docversion"
    assert with_sslmode(url, "require").endswith("?application_name=docversion&sslmode=require")


def test_seq_roundtrip_keeps_credentials_intact() -> None:
    url = "postgresql+asyncpg://postgres.abc.supabase.co:6543/postgres?sslmode=require"
    sync_url = async_to_sync(with_sslmode(url, "require"))
    assert sync_url == (
        "postgresql+psycopg2://postgres.abc.supabase.co:6543/postgres?sslmode=require"
    )