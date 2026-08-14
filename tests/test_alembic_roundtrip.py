import os
import subprocess
import sys

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import ALL_TABLES


@pytest.mark.anyio
async def test_alembic_upgrade_head_then_downgrade_minus_one(database_url: str) -> None:
    env = {**os.environ, "DOCVERSION_DATABASE_URL": database_url}

    def run_alembic(*args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"alembic {args} failed:\n{result.stdout}\n{result.stderr}"
        return result

    run_alembic("downgrade", "-1")

    current = run_alembic("current")
    assert "0005" in current.stdout
    assert "fe69f7664515" not in current.stdout

    run_alembic("upgrade", "head")

    current = run_alembic("current")
    assert "fe69f7664515" in current.stdout

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
    finally:
        await engine.dispose()

    for table in ALL_TABLES:
        assert table in tables, f"table {table} missing after alembic round-trip"
