# Backend Sprint Implementation Plan

**Project:** DocVersion backend (from `design_spec.md` — the authoritative design contract)
**Date:** 2026-08-01
**Level:** 3 (27 stories, multi-sprint)
**Team capacity:** 1 developer, 2-week sprints, ~30-35 pts/sprint
**Scope:** Backend application code only — FastAPI, data model, scheduler, adapters, diff engine, LLM client, publishing. Ops/deployment (systemd, Nginx, Playwright provisioning, backups) deferred to a separate plan.

## Status

| Sprint | Goal | Points | Status |
|---|---|---|---|
| Sprint 1 | Foundations + Auth | 32 | Planned |
| Sprint 2 | Tenancy + Sources + Scheduler core | 32 | Planned |
| Sprint 3 | Adapters, Diff, LLM, DB Publish | 29 | Planned |
| Sprint 4 | Publishing, Docs API, Hardening, E2E | 26 | Planned |

**Total: 27 stories, ~119 pts ≈ 8 weeks**

## Epic → Story inventory

| Epic | Stories | Pts |
|---|---|---|
| E1 Foundations & DB | 001-004 | 12 |
| E2 Auth & Tenancy | 005-011 | 28 |
| E3 Sources & Run-now | 012-013 | 8 |
| E4 Scheduler & Pipeline | 014-016 | 16 |
| E5 Adapters & Diff | 017-019 | 16 |
| E6 LLM (NVIDIA) | 020-021 | 10 |
| E7 Publishing & Docs API | 022-024 | 13 |
| E8 Hardening & E2E | 025-027 | 16 |

---

## Sprint 1 — Foundations + Auth (32 pts)

**Goal: Bootstrapped app, DB schema, working login/register.**

| ID | Story | Pts | Deps |
|---|---|---|---|
| 001 | Project scaffold — FastAPI entrypoint, env config, logging, `venv` deps | 2 | — |
| 002 | Async SQLAlchemy engine/session + `/health` | 3 | 001 |
| 003 | Alembic bootstrap + initial migration: all §6 tables + both required indexes | 5 | 002 |
| 004 | Base model mixins, JSONB support, passlib argon2id context | 2 | 002 |
| 005 | JWT access/refresh token service + `refresh_tokens` blocklist | 5 | 003 |
| 006 | Register + email verification (Brevo via stdlib `smtplib`) | 5 | 005 |
| 007 | Login + rate limit (`slowapi`) + logout | 3 | 005 |
| 008 | Refresh rotation endpoint | 2 | 005 |
| 009 | Password reset flow | 5 | 006 |

## Sprint 2 — Tenancy + Sources + Scheduler core (32 pts)

**Goal: Org-scoped CRUD, manual runs, and the scheduler pipeline skeleton.**

| ID | Story | Pts | Deps |
|---|---|---|---|
| 010 | Auth dependency: resolve org from JWT, `org_id` scoping + role guard (403) | 5 | 005 |
| 011 | Org invite (`owner`/`admin`) + membership management | 3 | 010 |
| 012 | Sources CRUD endpoints | 5 | 010 |
| 013 | `run-now` endpoint + per-org rate limit/input validation | 3 | 012 |
| 014 | APScheduler + `SQLAlchemyJobStore` + per-source jobs at `fetch_interval_seconds` | 5 | 012 |
| 015 | Per-source lock + `run_logs` recording + pipeline skeleton (fetch→hash→diff→publish) | 3 | 014 |
| 016 | Playwright worker pool (semaphore 3-5) + SSRF target validation (block RFC1918/link-local/`169.254.169.254`) | 8 | 014 |

## Sprint 3 — Adapters, Diff, LLM, DB Publish (29 pts)

**Goal: Real pipeline — from fetch to published doc.**

| ID | Story | Pts | Deps |
|---|---|---|---|
| 017 | OpenAPI adapter (fetch, canonicalize/hash) + `oasdiff` changelog → `diffs.diff_payload` | 8 | 015 |
| 018 | Scrape/webapp adapter: normalization, `css_scope_selector`, snapshot + raw file write | 5 | 016 |
| 019 | `difflib` text diff + triviality heuristic (skip LLM on cosmetic) | 3 | 018 |
| 020 | NVIDIA LLM client + Fernet-encrypted key at rest + structured output contract (`{section_key, new_content, reason}` — section-only apply) | 5 | 019 |
| 021 | LLM throttle/backoff + queue + `org_llm_usage` metering | 5 | 020 |
| 022 | DB publish: update `docs.current_content_md` + append `doc_updates` | 3 | 020 |

## Sprint 4 — Publishing, Docs API, Hardening, E2E (26 pts)

**Goal: Complete, verified system.**

| ID | Story | Pts | Deps |
|---|---|---|---|
| 023 | Git export mirror (commit/push, `last_git_export_commit`, failures log but never block DB publish) | 5 | 022 |
| 024 | Docs read API (list/get/version history/diff view) + resolve org by custom domain | 5 | 022 |
| 025 | Retention/cleanup job (90-day snapshots/diffs) + quota enforcement hooks on `org_llm_usage` | 3 | 021 |
| 026 | Security hardening pass — rate limits, key handling audit (never log/expose), error/exception hygiene | 5 | 010 |
| 027 | End-to-end pipeline verification (single source through fetch→publish; Alembic `upgrade`/`downgrade -1` round-trip test) | 8 | 022-026 |

---

## Cross-cutting rules (applied every sprint)

- **DoD per story:** code complete, tests passing, Alembic migration committed with tested `downgrade()`, no `org_id` from client trusted, no Playwright/LLM in request handlers.
- **Tenancy:** every query scoped at ORM layer (STORY-010's dependency is mandatory before any CRUD ships).
- **Dependency ordering is fixed** — S1 completes foundations and auth before anything else; adapters never precede the scheduler.

## Open items / risks

- **NVIDIA free-tier rate ceiling** is unspecified in the spec (§13) — STORY-021 sizing is a placeholder; confirm published `Llama-3.3-70B` limits before Sprint 3.
- `requirements.txt` is `fastapi`-only; deps (apscheduler, sqlalchemy, alembic, passlib, oasdiff/playwright, openai, cryptography) get added as stories land.
- **Deferred (explicitly out):** systemd units, Nginx/SNI custom domains, `playwright install chromium`, `pg_dump` backups — recommend a follow-up ops plan after Sprint 4.
