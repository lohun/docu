import os
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from collections.abc import AsyncIterator

# Route settings away from the developer's local .env (which may point at a
# production database/secret). Tests must be hermetic and deterministic.
_empty_env = tempfile.NamedTemporaryFile(
    prefix="docversion-test-env-", suffix=".env", dir="/tmp"
)
os.environ["DOCVERSION_ENV_FILE"] = _empty_env.name

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.csrf import csrf_cookie_name
from app.db import get_session
from app.main import create_app
from app.rate_limit import limiter

STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CSRFClient(httpx.AsyncClient):
    """httpx client that mirrors the real SPA.

    Before the first state-changing request it performs the ``GET /auth/csrf``
    handshake (as the frontend does on load) then echoes the cookie value as the
    ``X-CSRF-Token`` header on every subsequent state-changing request.
    """

    async def send(
        self, request: httpx.Request, *args: object, **kwargs: object
    ) -> httpx.Response:
        if request.method.upper() in STATE_CHANGING_METHODS:
            # A fully-crafted request (explicit X-CSRF-Token) is sent as-is.
            if "x-csrf-token" in request.headers:
                return await super().send(request, *args, **kwargs)
            token = self.cookies.get(csrf_cookie_name())
            if token is None:
                handshake = self.build_request("GET", "/auth/csrf")
                await super().send(handshake)
                token = self.cookies.get(csrf_cookie_name())
            # send() forwards the request as-is. If the Cookie header predates
            # the handshake it won't carry the csrf cookie yet, and
            # set_cookie_header refuses to overwrite an existing header, so
            # recompute from the jar when the csrf cookie isn't present.
            if csrf_cookie_name() not in request.headers.get("cookie", ""):
                request.headers.pop("cookie", None)
                self.cookies.set_cookie_header(request)
            if token and "x-csrf-token" not in request.headers:
                request.headers["x-csrf-token"] = token
        return await super().send(request, *args, **kwargs)

PG_SYS_DIR = Path("/usr/lib/postgresql")
PG_BIN_DIRS = sorted(PG_SYS_DIR.glob("*/bin")) if PG_SYS_DIR.exists() else []


def _pg_bin(name: str) -> str | None:
    for d in PG_BIN_DIRS:
        p = d / name
        if p.exists():
            return str(p)
    return shutil.which(name)


POSTGRES_AVAILABLE = all(_pg_bin(n) for n in ("postgres", "initdb", "pg_ctl", "createdb"))

ALL_TABLES = (
    "doc_updates",
    "run_logs",
    "diffs",
    "snapshots",
    "sources",
    "docs",
    "refresh_tokens",
    "org_memberships",
    "users",
    "org_llm_usage",
    "organizations",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def database_url() -> str:
    external = os.environ.get("DOCVERSION_TEST_DATABASE_URL")
    if external:
        _run_alembic_for_url(external)
        yield external
        return

    if not POSTGRES_AVAILABLE:
        pytest.skip("PostgreSQL server binaries not found; DB tests skipped")
    base = Path("/tmp") / f"docversion-pg-{uuid.uuid4().hex[:8]}"
    data_dir = base / "data"
    socket_dir = base / "sock"
    port = _free_port()
    url = f"postgresql+asyncpg://docversion@127.0.0.1:{port}/docversion"

    subprocess.run(
        [_pg_bin("initdb"), "-D", str(data_dir), "-A", "trust", "-U", "docversion"],
        check=True,
        capture_output=True,
    )
    socket_dir.mkdir()
    subprocess.run(
        [
            _pg_bin("pg_ctl"),
            "-D",
            str(data_dir),
            "-o",
            f"-p {port} -h 127.0.0.1 -k {socket_dir}",
            "-l",
            str(base / "pg.log"),
            "start",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            _pg_bin("createdb"),
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "docversion",
            "docversion",
        ],
        check=True,
        capture_output=True,
    )
    _run_alembic_for_url(url)
    try:
        yield url
    finally:
        subprocess.run(
            [_pg_bin("pg_ctl"), "-D", str(data_dir), "stop", "-m", "fast"],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(base, ignore_errors=True)


def _run_alembic_for_url(url: str) -> None:
    env = {**os.environ, "DOCVERSION_DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        env=env,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


@pytest.fixture()
async def session_factory(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE TABLE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE")
        )
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    yield
    limiter.reset()


@pytest.fixture()
async def client(session_factory) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def override_get_session() -> AsyncIterator:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = httpx.ASGITransport(app=app)
    async with CSRFClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
