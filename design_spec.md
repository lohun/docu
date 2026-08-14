# System Specification & Design Document
## Automated API/Web Version Tracking & Documentation Update Platform

**Status:** Design only — no implementation in this phase
**Audience:** Engineering team
**Last updated:** 2026-08-01

---

## 1. Overview

The system monitors versioned sources — an organization's own API specs, web applications, and third-party sites — for changes, and automatically generates and publishes updated technical documentation using an LLM. It is a multi-tenant, self-hosted web application.

**Core capabilities:**
- Register sources (OpenAPI spec URL, web app build endpoint, or arbitrary third-party URL) per organization.
- Periodically fetch each source, hash it, and detect meaningful changes.
- Compute a structured diff of what changed.
- Generate an updated documentation section via an LLM (NVIDIA API Catalog) and publish it automatically.
- Provide a web UI (React) for managing sources, reviewing history, and browsing generated docs.

---

## 2. Scope & Assumptions

| Area | Decision |
|---|---|
| Tenancy | Multi-tenant: multiple external orgs/users, each with isolated sources, docs, and API keys |
| Auth | Local (self-managed) login — no third-party OAuth/SSO in v1 |
| LLM | NVIDIA API Catalog (`https://integrate.api.nvidia.com/v1`), OpenAI-compatible chat completions API, key format `nvapi-...` |
| Scraping | Playwright, for both third-party sites and any web app source that requires JS rendering |
| Frontend | React + Vite |
| Backend | FastAPI + PostgreSQL (managed via Supabase — see §6.2) |
| Deployment | Bare metal / VM — packages installed directly, no Docker/Kubernetes; Postgres is a managed Supabase project, not a local service |
| Doc publishing | Auto-publish (no human-approval gate); dual target — database-backed store and git export (see §7.6) |
| LLM key model | Platform-shared NVIDIA API key, metered per org (no bring-your-own-key in v1) |
| Default LLM model | `Llama-3.3-70B` via NVIDIA NIM (free tier) |
| Email delivery | Brevo, via SMTP |
| Docs delivery | Both shared app URL (`/org/:slug/docs`) and org custom domains |
| Migrations | Alembic |
| Password hashing | `passlib`, argon2id scheme |

---

## 3. High-Level Architecture

```
┌─────────────────────┐        ┌──────────────────────────────┐
│   React + Vite SPA   │ HTTPS  │        FastAPI backend         │
│  (served via Nginx   │◄──────►│  - REST API (auth, CRUD,       │
│   or Vite preview)    │        │    sources, docs, runs)        │
└──────────────────────┘        │  - Scheduler process           │
                                 │  - Diff engine                 │
                                 │  - LLM client (NVIDIA)         │
                                 │  - Playwright worker           │
                                 └───────────────┬────────────────┘
                                                  │
                                         ┌────────▼────────┐
                                         │   PostgreSQL     │
                                         │  (orgs, users,    │
                                         │   sources, hashes,│
                                         │   diffs, docs,     │
                                         │   run logs)        │
                                         └────────────────────┘
```

**Process layout on the host machine (no containers):**
- `uvicorn` (FastAPI app, N workers) — behind Nginx as reverse proxy/TLS terminator, or run directly with `--workers`.
- A separate long-running **scheduler process** (APScheduler or a custom asyncio loop) that owns the fetch → hash → diff → AI → publish pipeline. Kept separate from the request-serving API workers so a slow scrape doesn't block user requests.
- A **Playwright worker pool** (subprocess(es) managed by the scheduler) — Playwright browsers are heavy, so this runs as its own managed process, not inline in an API request handler.
- PostgreSQL as a native system service.
- Both the API and scheduler run as **systemd services** for process supervision, restart-on-failure, and boot persistence, given there's no container orchestrator to do this instead.

---

## 4. Multi-Tenancy Model

- **Organization** is the top-level tenant boundary. All sources, documentation, API keys, and run history belong to an org.
- **User** belongs to one or more orgs, with a **role** per org: `owner`, `admin`, `member`, `viewer`.
- All backend queries are scoped by `org_id` — enforced at the ORM/query layer (never trust `org_id` from the client; derive it from the authenticated session/JWT).
- Per-org resource limits (source count, scrape frequency floor, LLM token budget) should be modeled from day one even if not enforced immediately — retrofitting quota fields is painful.
- LLM access uses a single **platform-owned NVIDIA API key** (not per-org keys). Every org's usage is metered against it — token counts and call counts tracked per org per billing period — so quota enforcement lives entirely in the app layer, not in NVIDIA-side key scoping.

---

## 5. Authentication & Authorization

**Type:** Local username/password authentication (no external IdP).

- Passwords hashed with **`passlib`**, using the **argon2id** scheme (`passlib.hash.argon2`) — preferred over bcrypt for new systems. `passlib`'s `CryptContext` also gives a clean upgrade path if the hashing scheme ever needs to change later (it can verify old hashes and re-hash on next login).
- Session strategy: **JWT access token (short-lived, ~15 min) + refresh token (httpOnly, secure cookie, longer-lived, rotated on use)**. Avoids storing raw sessions in Postgres for every request while still allowing revocation via a refresh-token blocklist table.
- Endpoints: `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/invite` (org owner invites a user by email + assigns role).
- **Authorization** enforced via a FastAPI dependency that resolves the current user + their role in the `org_id` path/query param, raising 403 if the role is insufficient for the action (e.g. only `owner`/`admin` can add/delete sources or rotate the org's LLM key).
- Rate-limit `/auth/login` (e.g. via `slowapi`) to blunt credential-stuffing given this is public-facing multi-tenant auth.
- Email verification and password-reset flows should be included in scope even in v1, since this is external-facing — an account system without password reset is a support burden from day one.
- Transactional email (verification, password reset, org invites) sent via **Brevo SMTP**. Backend uses Python's standard `smtplib`/`email.message` (or a thin wrapper) against Brevo's SMTP relay, with SMTP credentials stored in the same restricted-permission env file as other secrets (see §10). Sender domain should have SPF/DKIM configured on Brevo's side before launch, or verification emails risk landing in spam.

---

## 6. Data Model (PostgreSQL)

Core tables (columns abbreviated to the essential ones):

```
organizations(id, name, slug, created_at, plan_tier,
              custom_domain NULL, custom_domain_verified_at NULL)

org_llm_usage(id, org_id, period_start, period_end,
              tokens_used, call_count, token_quota)

users(id, email, password_hash, created_at, is_active)

org_memberships(user_id, org_id, role, invited_at, accepted_at)

sources(id, org_id, name, type[openapi|webapp|scrape], target_url,
        fetch_interval_seconds, css_scope_selector NULL,
        doc_target_id, is_active, created_at)

snapshots(id, source_id, content_hash, normalized_excerpt,
          fetched_at, raw_storage_ref)

diffs(id, source_id, from_snapshot_id, to_snapshot_id,
      diff_type[oasdiff|text], diff_payload JSONB,
      is_trivial, created_at)

doc_updates(id, source_id, diff_id, doc_id, section_key,
            previous_content, new_content, llm_model_used,
            token_usage, status[published|rejected|error],
            created_at)

docs(id, org_id, source_id, title, slug, current_content_md,
     version, updated_at, git_export_enabled, git_export_path NULL,
     last_git_export_commit NULL)

run_logs(id, source_id, started_at, finished_at, outcome,
         error_message NULL)

refresh_tokens(id, user_id, token_hash, expires_at, revoked_at NULL)
```

### 6.1 Migrations

- **Alembic** manages all schema changes, run against SQLAlchemy models — no hand-run DDL against production.
- `alembic/versions/` is committed to source control alongside the models; every schema change ships as a reviewed migration file, not an ad-hoc `ALTER TABLE`.
- Migrations are applied as an explicit deployment step (`alembic upgrade head`), run manually or via a `systemd` `ExecStartPre` on the API service unit so the schema is guaranteed current before the app starts — given there's no orchestrator to sequence this automatically.
- Every migration should include a tested `downgrade()` path; this matters more on bare metal, where rolling back a bad migration means running `alembic downgrade -1` directly against production rather than redeploying a previous container image.

### 6.2 Database provider: Supabase (managed Postgres)

The system runs on PostgreSQL provided by **Supabase** (managed hosting) rather than a self-managed local Postgres. Supabase *is* PostgreSQL, so the SQLAlchemy models, migrations, and tenancy model are unchanged — only the connection/ops layer differs.

- **Connection:** connect through the **Supavisor pooler (port 6543, session mode)** via the `postgres.<ref>.<region>.pooler.supabase.com` host, TLS enforced (`sslmode=require`). Session mode is required because both the API engine and the APScheduler jobstore hold persistent pooled connections; the transaction/port-pooling mode would break connection reuse and per-source job locks.
- **Config:** `DOCVERSION_DATABASE_URL` (asyncpg URL) plus `DOCVERSION_DATABASE_SSLMODE` (empty = no forcing; `require` for Supabase). `app/db_urls.py` derives the psycopg2 sync URL (used by the scheduler jobstore) from the same URL and applies SSL per driver — `sslmode` is honoured by both asyncpg and psycopg2 from the query string. Pool sizing, connect timeout, and `pool_pre_ping` are configurable.
- **Extensions:** the schema uses only core PG constructs (`IDENTITY`, `JSONB`, boolean/text server defaults) — no extensions required, so no Supabase extension-whitelist concerns.
- **Free tier behaviour:** Supabase free projects can auto-pause compute after ~1 week of inactivity. The scheduler's continuous activity plus the heartbeat probe (see §10) normally prevent this, but the platform must treat DB reachability as alertable, not silent.
- **Backups:** free tier has no PITR/continuous backups. The DB is the source of truth, so keep the nightly external `pg_dump` cron (targeting the Supabase connection string) writing to local disk; Supabase-managed backups are additional, not relied upon.
- **Rollback:** because the app talks only via `DOCVERSION_DATABASE_URL`, switching provider is a config change — point the env var back at a local Postgres and nothing else changes.

**Design notes:**
- `snapshots.raw_storage_ref` points to a file path on disk (e.g. `/var/lib/docversion/snapshots/{id}.raw`) rather than storing large raw HTML/spec blobs directly in Postgres — keeps the DB lean; only the normalized excerpt and hash live in-row for querying.
- `diffs.diff_payload` as JSONB lets you store oasdiff's structured changelog or a text-diff payload uniformly.
- `doc_updates` is an append-only audit trail; `docs.current_content_md` is the materialized "latest" view the frontend reads. This separation lets you show full history without recomputing it from diffs each time.
- Add a Postgres index on `(source_id, fetched_at desc)` for snapshots and `(org_id, updated_at desc)` for docs — these are the hot query paths for the dashboard.

---

## 7. Backend Design (FastAPI)

### 7.1 API surface (representative, not exhaustive)

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/*` | Auth flows (see §5) |
| GET/POST | `/orgs/{org_id}/sources` | List/create tracked sources |
| PATCH/DELETE | `/orgs/{org_id}/sources/{id}` | Update/deactivate a source |
| POST | `/orgs/{org_id}/sources/{id}/run-now` | Manually trigger a fetch/diff/publish cycle |
| GET | `/orgs/{org_id}/sources/{id}/runs` | Run history for a source |
| GET | `/orgs/{org_id}/docs` | List generated docs |
| GET | `/orgs/{org_id}/docs/{id}` | Doc content + version history |
| GET | `/orgs/{org_id}/docs/{id}/diffs/{diff_id}` | View a specific diff and the doc change it produced |
| GET/PATCH | `/orgs/{org_id}/settings` | LLM key, scrape defaults, quotas |

### 7.2 Source adapters

Same three-adapter shape as previously discussed, implemented as FastAPI-independent Python modules invoked by the scheduler (not by request handlers):

- **OpenAPI adapter** — HTTP fetch of the spec, canonicalize (sort keys, normalize YAML→JSON) before hashing.
- **Web app adapter** — fetch a build manifest/asset-fingerprint if the org exposes one; otherwise fall back to the scrape adapter with Playwright.
- **Scrape adapter (Playwright)** — see §9.

### 7.3 Diff engine

- OpenAPI sources: shell out to `oasdiff` (or the Python bindings if available) against the two normalized spec snapshots; parse its structured changelog into `diffs.diff_payload`.
- Web app / scrape sources: unified text diff (Python `difflib`) over the normalized content, with a "triviality" heuristic (e.g. change ratio below a threshold, or affecting only excluded regions) to avoid invoking the LLM on cosmetic changes.

### 7.4 Scheduler

- APScheduler running inside the dedicated scheduler process, with a job per active source at its configured `fetch_interval_seconds`, backed by Postgres (`SQLAlchemyJobStore`) so schedule state survives restarts.
- Each job run: fetch → hash compare → (if changed) diff → (if non-trivial) LLM call → publish → write `run_logs` row. Wrap in a per-source lock to avoid overlapping runs if a fetch is slow.
- Concurrency cap on simultaneous Playwright-based jobs (e.g. semaphore of 3–5) since headless browsers are memory-heavy — this matters more on bare metal with no orchestrator to isolate resource spikes.

### 7.5 LLM integration (NVIDIA API Catalog)

- Client: OpenAI Python SDK pointed at NVIDIA's endpoint —
  `base_url="https://integrate.api.nvidia.com/v1"`, `api_key=<platform nvapi-... key>`.
- **Single platform key**, stored **encrypted at rest** (e.g. via `cryptography.Fernet` with a server-side master key from the environment/secrets file, not committed to the repo) — not exposed to or configurable by orgs.
- **Default model: `Llama-3.3-70B` via NVIDIA NIM (free tier).** Since this is the free tier, design for its rate limits explicitly:
  - Apply client-side request throttling/backoff in the scheduler so concurrent doc-generation jobs across orgs don't burst past NVIDIA's free-tier rate limit.
  - Queue LLM calls (simple in-process queue or a Postgres-backed job table) rather than firing them directly from parallel scheduler jobs, so a busy period degrades to "slower processing" rather than failed calls.
  - Because it's a shared free-tier key, `org_llm_usage` (see §6) is what enforces fairness between orgs — not NVIDIA-side quota, which is platform-wide.
  - Revisit the free-tier assumption before scaling past whatever request-per-minute ceiling NVIDIA publishes for the tier at the time — this should be a launch-readiness checklist item, not just a design note.
- Model is otherwise fixed/global (not per-org configurable) in v1, consistent with the single shared-key model.
- Prompt contract: force structured JSON output — `{section_key, new_content, reason}` — and apply the returned content only to that section of `docs.current_content_md`, never let the model return or replace the full document. This is the primary guardrail given publishing is automatic with no human review.
- Log `token_usage` and `call_count` per call into `org_llm_usage` for per-org quota accounting, and `llm_model_used`/`token_usage` per doc update for audit purposes.

### 7.6 Publishing & Docs Delivery

Publish target is **both** database-backed and git-export, not either/or:

- **Database-backed (primary, always on):** every auto-publish writes `docs.current_content_md` and appends a `doc_updates` row. This is what the frontend renders and is the source of truth for the app.
- **Git export (optional per doc, `docs.git_export_enabled`):** after a successful DB publish, the same content is written to a file under a per-org path on disk (`/var/lib/docversion/git-exports/{org_slug}/`) and committed/pushed to a git remote the org configures. `docs.last_git_export_commit` tracks the last successful export commit for drift detection/retry. Git export failures are logged but never block the DB publish — DB is authoritative, git is a mirror.
- **Docs delivery — both routing modes supported:**
  - Shared app URL: `https://<platform-domain>/org/:slug/docs/:doc_slug` — always available, no setup needed.
  - Custom domain: org sets `organizations.custom_domain` and points DNS (CNAME) at the platform; Nginx uses SNI-based virtual host routing to map the incoming `Host` header to the correct `org_id`, and the FastAPI docs-read endpoint resolves org by domain instead of by `org_slug` path segment for those requests. TLS for custom domains via Let's Encrypt with DNS or HTTP-01 challenge per verified domain — `custom_domain_verified_at` gates certificate issuance so the platform doesn't request certs for unverified/unowned domains.

---

## 8. Frontend Design (React + Vite)

### 8.1 Structure
- Vite + React, TypeScript recommended for a system with this much cross-cutting state (org/role context, source config forms).
- Routing: `react-router`. Top-level routes: `/login`, `/register`, `/org/:orgId/sources`, `/org/:orgId/sources/:id`, `/org/:orgId/docs`, `/org/:orgId/docs/:id`, `/org/:orgId/settings`.
- State/data-fetching: `@tanstack/react-query` for server state (sources, docs, run history) — avoids hand-rolled caching/loading logic given most screens are "fetch from API, show table/detail."
- Auth state: access token held in memory (React context), refresh token in an httpOnly cookie — access token is never persisted to `localStorage` to reduce XSS token-theft exposure.

### 8.2 Key screens
- **Source list** — table of tracked sources per org, status (last run, last hash-changed time), manual "run now" action.
- **Source detail** — run history timeline, diff viewer (structured for OpenAPI, unified-diff view for text), links to the doc updates it produced.
- **Docs browser** — the generated documentation set per org, rendered from `current_content_md`, with a version-history side panel pulling from `doc_updates`.
- **Org settings** — LLM usage/quota dashboard (read-only, sourced from `org_llm_usage`), scrape defaults, member management/roles, custom domain setup (DNS instructions + verification status), and per-doc git export configuration (remote URL, enable/disable toggle, last export status).

---

## 9. Web Scraping Design (Playwright)

- Used for: (a) third-party sites with no API, and (b) internal web apps without an exposed build manifest.
- Runs as **Python Playwright** (not the Node variant) inside the scheduler process's worker pool, using `chromium` headless.
- **Normalization before hashing** is the most important part of this component — strip `<script>`, ads/analytics blocks, timestamps, and session-specific attributes; support an optional per-source CSS selector (`sources.css_scope_selector`) so orgs can scope hashing/diffing to just the content region that matters (e.g. `#api-reference`), avoiding false positives from unrelated page churn.
- Each scrape stores a full-page screenshot alongside the text extract (`raw_storage_ref`) — useful for a human spot-checking a diff later even though there's no approval gate in the publish flow.
- Respect `robots.txt` and reasonable rate limits for third-party sources; this should be a per-source configurable politeness delay, not hardcoded, since orgs will scrape a mix of their own and external sites with different tolerances.
- Browser binaries (`playwright install chromium`) must be provisioned as part of host setup, not per-request — document this explicitly in deployment steps since it's a common bare-metal setup gap.

---

## 10. Deployment (Bare Metal)

No Docker/Kubernetes — all services installed directly on the host.

| Component | How it runs |
|---|---|
| PostgreSQL | **Managed by Supabase** (no local service). One project per environment; connect via the Supavisor pooler (session mode, port 6543) with `DOCVERSION_DATABASE_SSLMODE=require`. Whitelist the host's IP and pick a region near the VM |
| Schema migrations | Alembic, run via `alembic upgrade head` as an explicit pre-start deployment step |
| FastAPI app | `uvicorn` under a `systemd` unit, `--workers N` |
| Scheduler | Separate Python process, its own `systemd` unit |
| Reverse proxy / TLS | Nginx in front of `uvicorn`, terminating HTTPS (Let's Encrypt/certbot) |
| Frontend | `vite build` static output served by Nginx (or a small static file server) |
| Playwright | `pip install playwright && playwright install chromium` on the same host as the scheduler |
| Email | Brevo SMTP relay — outbound only, credentials in the env file, no local mail server needed |
| Custom domain TLS | `certbot` run per verified org custom domain (DNS or HTTP-01 challenge), certs auto-renewed via `certbot`'s systemd timer; Nginx config generated/reloaded per newly verified domain |

**Process supervision:** since there's no orchestrator to restart crashed containers, `systemd` (`Restart=on-failure`) is the safety net for both the API and scheduler units — this should be treated as a hard requirement, not an afterthought, given a crashed scheduler silently means "nothing gets tracked" until someone notices. Additionally run `scripts/db_heartbeat.sh` on a short `systemd` timer so an unreachable/paused Supabase database surfaces immediately instead of silently stalling the pipeline.

**Secrets:** environment file (`/etc/docversion/.env`, file-permission-restricted) for the Supabase DB URL, JWT signing secret, and the Fernet master key used to encrypt org NVIDIA API keys — never in source control.

**Backups:** the DB is the sole source of truth for everything except raw snapshot blobs on disk (which should be backed up too, or treated as regenerable/non-critical). Free-tier Supabase has no PITR, so run `scripts/backup_db.sh` nightly via a `systemd` timer/cron (custom-format `pg_dump` to local disk, pruned after N days); Supabase-managed backups are additional if the project is upgraded.

---

## 11. Security Considerations

- Per-org data isolation enforced at the query layer — treat cross-tenant data leakage as the top risk given this is external-facing multi-tenant software.
- Encrypt the platform NVIDIA API key at rest; never log it; it is never exposed via any API response (it's a platform secret, not an org-visible value).
- Custom domains: verify domain ownership (DNS TXT challenge) before issuing TLS certs or routing traffic — otherwise an org could claim a domain it doesn't control, or one org could squat a domain intended for another.
- Auto-publish without human review is an accepted product decision, but it means the LLM's structured-output contract (§7.5) and the triviality/validation checks in the diff engine are load-bearing for output quality — not optional polish.
- Playwright fetches of arbitrary org-submitted URLs (SSRF risk): restrict outbound fetch targets to a URL allowlist/validation step, block internal/private IP ranges (RFC1918, link-local, cloud metadata endpoint `169.254.169.254`) so a malicious source URL can't be used to probe internal infrastructure from your server.
- Rate-limit and input-validate the manual "run now" endpoint per org to prevent one tenant exhausting shared Playwright/LLM capacity.

---

## 12. Non-Functional Requirements

| Requirement | Target (proposed — confirm with team) |
|---|---|
| Source check frequency | Configurable per source, minimum interval floor (e.g. 5 min) to bound load |
| API latency (non-scrape endpoints) | p95 < 300ms |
| Scrape job timeout | 30s per page, configurable |
| LLM call timeout | 60s, with retry-once on transient failure |
| Data retention | Snapshots/diffs retained 90 days by default (configurable per org), docs retained indefinitely |

---

## 13. Decisions Confirmed (This Revision)

| Question | Decision |
|---|---|
| Publish target | Both — database-backed store (primary/authoritative) and optional per-doc git export (§7.6) |
| NVIDIA key model | Single platform-shared key, usage metered per org (§7.5, §6) — no bring-your-own-key in v1 |
| Email delivery | Brevo, via SMTP (§5) |
| Custom domains for docs | Both — shared app URL and org-configured custom domains supported (§7.6) |
| Database provider | Supabase managed Postgres (Supavisor pooler, session mode, TLS) across all environments; app-level tenancy and Alembic migrations unchanged (§6.2) |
| Default LLM model | `Llama-3.3-70B` via NVIDIA NIM, free tier (§7.5) — free-tier rate limits require explicit throttling/queueing design, and should be reassessed before scale |

**Remaining open item:** the free-tier rate limit ceiling for `Llama-3.3-70B` on NVIDIA NIM isn't specified here since it's subject to change — confirm the current published limit at implementation time and size the throttling/queue design (§7.5) against it.

---

## 14. Explicitly Out of Scope (this phase)

- Any code implementation.
- CI/CD pipeline design.
- Containerization (explicitly excluded per requirements).
- SSO/OAuth login providers.
- Human-in-the-loop review/approval UI for doc updates (auto-publish only, per current decision).
