# AGENTS.md

## Project status
- Design-only phase — no implementation exists. `design_spec.md` (repo root) is the authoritative design contract. Read it fully before writing backend code.
- Backend scope: FastAPI API, APScheduler scheduler process, diff engine, NVIDIA LLM client, Python Playwright worker. React frontend is out of scope.
- Deps: `requirements.txt` (currently only `fastapi`). Use `venv/` in repo root, not system Python.
- Bare-metal deployment: no Docker/Kubernetes. Assume systemd units, Nginx reverse proxy, native PostgreSQL.

## Tenancy (non-negotiable)
- Org is the top-level tenant boundary; per-org roles `owner|admin|member|viewer`.
- Never trust `org_id` from the client — derive it from the authenticated JWT/session and enforce scoping at the ORM/query layer.
- Model per-org quota fields (`org_llm_usage`, limits) from day one, even if unenforced initially.

## Auth
- Local username/password only (no OAuth/SSO v1). Hash with passlib **argon2id** (`passlib.hash.argon2`), not bcrypt.
- JWT access token (~15 min) + refresh token in httpOnly secure cookie, rotated on use, backed by `refresh_tokens` blocklist table.
- Email verification + password reset in v1; mail via Brevo SMTP using stdlib `smtplib`. Rate-limit `/auth/login`.

## Process architecture
- Scheduler process (APScheduler, `SQLAlchemyJobStore` in Postgres) owns the whole fetch → hash → diff → LLM → publish pipeline.
- API request handlers never run the pipeline, scraping, or Playwright inline. Playwright runs in a managed worker pool with a concurrency cap (semaphore ~3–5).
- Per-source lock to prevent overlapping runs; write a `run_logs` row per run.

## Data model rules
- Alembic for all schema changes; migration files committed with models, each with a tested `downgrade()`. `alembic upgrade head` is an explicit pre-start deploy step.
- `snapshots.raw_storage_ref` = file path on disk (e.g. `/var/lib/docversion/snapshots/{id}.raw`) — never store raw HTML/spec blobs in Postgres.
- `docs.current_content_md` is the materialized "latest" view; `doc_updates` is the append-only audit trail. Never recompute history from diffs.
- Indexes required: `(source_id, fetched_at desc)` on snapshots, `(org_id, updated_at desc)` on docs.

## Diff engine
- OpenAPI sources: `oasdiff` on normalized spec snapshots → changelog into `diffs.diff_payload` (JSONB).
- Webapp/scrape sources: `difflib` text diff with a triviality heuristic — skip the LLM on cosmetic changes.
- Source adapters are FastAPI-independent modules invoked by the scheduler, never by request handlers.

## LLM (NVIDIA)
- OpenAI SDK, `base_url="https://integrate.api.nvidia.com/v1"`, key prefix `nvapi-...`, default model `Llama-3.3-70B` (free tier).
- Single platform-shared key, encrypted at rest (`cryptography.Fernet`, master key in `/etc/docversion/.env`). Never logged, never in API responses, not per-org configurable.
- Free-tier limits: throttle/backoff and queue LLM calls (don't fire from parallel jobs); record `token_usage`/`call_count` into `org_llm_usage`.
- Structured-output contract: model returns `{section_key, new_content, reason}`; apply only to that section of `docs.current_content_md` — never let the model replace the full document.

## Publishing
- Auto-publish, no human review gate. DB is authoritative; git export is a mirror — export failures log but never block DB publish.
- Custom domains: Nginx SNI routing, `custom_domain_verified_at` gates cert issuance; resolve org by domain (not `:slug`) for those doc requests.

## Security
- Cross-tenant leakage is the top risk (see Tenancy).
- SSRF: validate/allowlist Playwright fetch targets; block private / link-local IP ranges and `169.254.169.254` metadata endpoint.
- Rate-limit and input-validate `POST .../run-now` per org.

## NFRs (proposed — confirm at implementation)
- Scrape timeout 30 s/page; LLM call timeout 60 s with retry-once; min `fetch_interval_seconds` floor 5 min; snapshots/diffs retained 90 days (docs kept indefinitely).
