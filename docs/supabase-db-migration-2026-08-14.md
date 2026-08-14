# Supabase (Managed Postgres) Migration — Implementation Record

**Date:** 2026-08-14
**Status:** Implemented (code + config + ops scripts + docs). Provisioning of live Supabase project(s) is the remaining manual/ops step — requires the account holder to create the project(s) and supply the pooler connection string.

## 1. Summary

Switched the database provider from self-managed PostgreSQL to **Supabase managed Postgres** while keeping the application architecture unchanged. Because Supabase is PostgreSQL, this was a connection/ops swap, not a rewrite: SQLAlchemy models, Alembic migrations, tenancy model, pipeline, and scheduler are untouched. App-level auth (users/argon2id/JWT) and app-level tenancy remain exactly as before — no Supabase Auth/RLS/Storage was adopted.

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Managed Postgres only | Keep the system as-is |
| Environments | Supabase in all envs (dev/staging/prod) | Parity; per-env project(s) |
| Tier | Free (with documented auto-pause risk) | Cost; heartbeat mitigates pause |
| Data | Greenfield fresh schema | No existing prod data to migrate |
| Connection | Supavisor pooler, session mode, port 6543 | Session mode preserves SQLAlchemy pooled connections + APScheduler job locks; pooler exposes IPv4 |
| TLS | `DOCVERSION_DATABASE_SSLMODE=require` | asyncpg and psycopg2 both honour `sslmode` from the URL query string |
| Alembic | Kept original committed `alembic/env.py` + `alembic.ini` behavior | User decision — the committed hardcoded-URL wiring is a deliberate repo choice; not changed here |

## 3. Changes

### Code
- **`app/db_urls.py` (new)** — `with_sslmode()` appends/merges an `sslmode` query param (used by both drivers); `async_to_sync()` converts an asyncpg URL to the equivalent psycopg2 URL preserving host/port/creds/query.
- **`app/config.py`** — added `database_sslmode`, `database_pool_size`, `database_max_overflow`, `database_connect_timeout_seconds`.
- **`app/db.py`** — `create_engine()` now uses the effective (sslmode-applied) URL and configurable pool size/overflow, `pool_pre_ping=True`, and connect timeout.
- **`app/scheduler/scheduler.py`** — `_sync_database_url` delegates to `async_to_sync`; constructor applies sslmode when a URL is provided (no-arg stays in-memory for tests).

### Ops scripts (new, `scripts/`)
- **`backup_db.sh`** — nightly custom-format `pg_dump` of `DOCVERSION_DATABASE_URL` to local disk, prunes after `BACKUP_RETENTION_DAYS` (default 14). Required because free-tier Supabase has no PITR and the DB is the source of truth.
- **`db_heartbeat.sh`** — cheap `SELECT 1` probe on a timer; exits non-zero if the DB is unreachable (including a paused free-tier project). Prevents a silently stalled scheduler.

### Config/docs
- **`.env.example`** — documented the Supabase pooler URL + `DOCVERSION_DATABASE_SSLMODE=require` and the new pool settings.
- **`requirements.txt`** — removed the unused `supabase` client package (never imported; not part of "keep as-is").
- **`.gitignore`** — removed a stray accidentally-committed line.
- **`design_spec.md`** — §2 scope row, new §6.2 (DB provider: Supabase), §10 deployment/backups/heartbeat, §13 decision row.
- **`AGENTS.md`** — updated DB-provider, connection, and ops-script guidance.

### Tests (new)
- **`tests/test_db_urls.py`** — URL builder unit tests (driver swap, query preservation, sslmode merge/replace, credential integrity).
- **`tests/test_db_parity.py`** — pins that migrations contain no `CREATE EXTENSION` and that the schema renders as core PG DDL (managed-PG compatible).
- **`conftest.py`** — supports `DOCVERSION_TEST_DATABASE_URL` to run the suite against an external DB (e.g. a Supabase staging project) while keeping the ephemeral local-PG fallback.

## 4. Test results

Full suite (excluding Playwright-heavy `test_playwright_pool.py` / `test_scrape_adapter.py`):

| Run | Failed | Errors | Passed |
|---|---|---|---|
| Pristine baseline (no changes) | 65 | 136 | 77 |
| With these changes, alembic reverted per user | 65 | 136 | 89 |

The failure/error set is **byte-identical** to the pristine baseline (`diff` of `FAILED/ERROR` lists is empty) — the 12 extra passes are the new unit/parity tests. The baseline failures are pre-existing (the committed `alembic/env.py` ignores `DOCVERSION_DATABASE_URL`, so the ephemeral test DB is never migrated → `relation "organizations" does not exist`; these were failing before any changes).

New tests all pass: `tests/test_db_urls.py` and `tests/test_db_parity.py` (12 tests).

`scripts/backup_db.sh` and `scripts/db_heartbeat.sh` both exercised against the local dev DB (success and failure paths).

## 5. Provisioning steps remaining (ops, requires Supabase account)

1. Create Supabase project(s): one non-prod (dev+staging+CI integration) and one prod; region near the VM; note the connection string `postgres.<ref>.<region>.pooler.supabase.com:6543`.
2. Set the DB password in the project; store it in `/etc/docversion/.env`:
   ```
   DOCVERSION_DATABASE_URL=postgresql+asyncpg://postgres.<ref>.<region>.pooler.supabase.com:6543/postgres
   DOCVERSION_DATABASE_SSLMODE=require
   ```
   (Password must be URL-encoded in the URL.)
3. If the pooler requires it, whitelist the host IP(s) in the Supabase network settings.
4. Fresh schema: `alembic upgrade head` against the project (the committed `alembic.ini` URL or an env override must target the Supabase URL).
5. Install the systemd timers for `scripts/backup_db.sh` (nightly) and `scripts/db_heartbeat.sh` (every few minutes).
6. Roll out API + scheduler with the env file; verify `/health` reports DB OK and a pipeline run persists.

## 6. Acceptance criteria

- [x] No DB credentials introduced into git (the previously-committed URL in `alembic.ini` is unchanged per user decision — flagged separately as a security follow-up).
- [x] URL helpers unit-tested; sync jobstore URL derived correctly.
- [x] Schema confirmed to be managed-PG-compatible (no extensions; core PG DDL).
- [x] Test suite regression-free vs pristine baseline.
- [x] Backup + heartbeat scripts present and exercised.
- [x] Design spec + AGENTS.md updated for managed Postgres.

## 7. Follow-ups / risks

- **Security:** `alembic.ini` still contains a committed DB credential (original repo state, retained per user instruction). Recommend rotating that password and moving the URL to env-driven wiring in a separate change.
- **Free-tier auto-pause:** mitigated by heartbeat + scheduler activity; escalate to Pro if the project ever pauses.
- **Live verification** against a real Supabase project (run the suite with `DOCVERSION_TEST_DATABASE_URL`, run `alembic upgrade head`, exercise a full pipeline run) is the remaining validation, blocked on credentials.
