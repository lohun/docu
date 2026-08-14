# Sprint 3 & 4 Implementation Plan (TDD-first)

**Project:** DocVersion backend  
**Date:** 2026-08-01  
**Authoritative contract:** `design_spec.md`  
**Sprint plan reference:** `docs/sprint-plan-backend-2026-08-01.md`  
**Prerequisite:** Sprint 1–2 complete (stories 001–016)

---

## Current State

Sprints 1–2 are complete. The backend has auth, tenancy, sources CRUD, run-now, a pipeline skeleton, and SSRF-safe scraping — but **none of the core product loop** (snapshot → diff → LLM → publish) exists yet.

| Area | Status |
|---|---|
| Auth, tenancy, memberships | Done |
| Sources CRUD + run-now | Done |
| DB schema (all §6 tables) | Done via migration `0001` |
| ORM models | Missing `DocUpdate`, `OrgLlmUsage` |
| Pipeline (`app/scheduler/pipeline.py`) | Fetch + hash only; no snapshots, diffs, LLM, publish |
| Adapters | None (`app/adapters/` does not exist) |
| Diff engine | None |
| LLM client | None |
| Publishing | None |
| Docs API | None |
| Git export | None |
| Scheduler | In-memory stub — not real APScheduler + `SQLAlchemyJobStore` |

The pipeline skeleton explicitly defers Sprint 3 work:

```python
# app/scheduler/pipeline.py (line ~69)
# Pipeline skeleton step (hash, diff, publish to land in Sprint 3)
run_log.outcome = "success"
```

**Sprint 3 goal:** Real pipeline — fetch → snapshot → diff → LLM → DB publish.  
**Sprint 4 goal:** Git mirror, docs read API, retention/quota, hardening, E2E verification.

---

## Technical Debt to Address First

Before starting story 017, resolve these Sprint 2 gaps that block a correct pipeline:

1. **Missing ORM models** — add `DocUpdate` and `OrgLlmUsage` (tables exist in `0001`, no Python models).
2. **Config gaps** — extend `Settings` with:
   - `snapshot_storage_dir` (default `/var/lib/docversion/snapshots`)
   - `git_export_base_dir`
   - `fernet_master_key` (for encrypted NVIDIA key at rest)
   - `nvidia_api_key_encrypted` / `nvidia_base_url` / `nvidia_model` (default `Llama-3.3-70B`)
   - `llm_call_timeout_seconds` (60), `scrape_timeout_seconds` (30)
3. **Scheduler stub** — `DocVersionScheduler` tracks jobs in a dict but never fires them. Wire APScheduler + `SQLAlchemyJobStore` in Phase 0 so scheduled runs exercise the full pipeline.
4. **`webapp` source type** — schema allows it; pipeline only handles `scrape` vs everything-else-as-HTTP. Add explicit routing in Sprint 3.
5. **First-run baseline** — when no prior snapshot exists, write snapshot and exit successfully without diff/LLM (establishes baseline hash).

---

## Guiding Principles

- Never trust `org_id` from the client. Derive it from the authenticated context and enforce scoping in the ORM/query layer.
- Source adapters, diff engine, LLM client, and publishing are **FastAPI-independent modules** invoked by the scheduler — never by request handlers.
- For every feature, write the failing test first, implement the smallest fix, run the targeted test slice, then refactor.
- `docs.current_content_md` is the materialized latest view; `doc_updates` is the append-only audit trail. Never recompute history from diffs.
- `snapshots.raw_storage_ref` is a file path on disk — never store raw HTML/spec blobs in Postgres.
- DB publish is authoritative; git export failures log but never block DB publish.

---

## TDD Workflow for Each Slice

For every story or bug fix:

1. Add or update a regression test in `tests/`.
2. Run the targeted test(s) and confirm they fail.
3. Implement the minimal production change.
4. Re-run the targeted test(s).
5. Run the broader related suite.
6. Only then refactor for clarity or reuse.

---

## New Dependencies (`requirements.txt`)

Add as stories land:

```
apscheduler          # wire real scheduler in Phase 0
playwright           # already optional-imported
openai               # NVIDIA-compatible client
cryptography         # Fernet key encryption
pyyaml               # OpenAPI YAML canonicalization
oasdiff              # or subprocess to oasdiff CLI — pick one approach
beautifulsoup4       # HTML normalization for scrape adapter
lxml                 # optional, faster HTML parsing
```

---

## Proposed Module Layout

```
app/
  adapters/
    __init__.py
    base.py              # AdapterResult(normalized, raw_bytes, excerpt)
    openapi.py           # STORY-017
    scrape.py            # STORY-018 (extract from worker_pool)
    webapp.py            # STORY-018 (manifest fingerprint → scrape fallback)
  diff/
    __init__.py
    openapi_diff.py      # oasdiff wrapper → JSONB payload
    text_diff.py         # difflib + triviality heuristic (STORY-019)
  llm/
    __init__.py
    client.py            # NVIDIA client + structured output (STORY-020)
    queue.py             # throttle/backoff/queue (STORY-021)
    key_store.py         # Fernet encrypt/decrypt at rest
  publish/
    __init__.py
    db_publish.py        # STORY-022
    git_export.py        # STORY-023
    section_apply.py     # apply {section_key, new_content} to markdown
  storage/
    snapshot_store.py    # write/read raw files to disk
  models/
    doc_update.py        # new
    org_llm_usage.py     # new
  routers/
    docs.py              # STORY-024
  jobs/
    retention.py         # STORY-025
    quota.py             # STORY-025 hooks
```

---

# Sprint 3 — Adapters, Diff, LLM, DB Publish (29 pts)

**Goal:** A source run produces a snapshot, detects meaningful change, calls the LLM, and publishes to `docs.current_content_md`.

| ID | Story | Pts | Deps |
|---|---|---|---|
| 017 | OpenAPI adapter + oasdiff changelog → `diffs.diff_payload` | 8 | 015 |
| 018 | Scrape/webapp adapter: normalization, css_scope, snapshot write | 5 | 016 |
| 019 | difflib text diff + triviality heuristic | 3 | 018 |
| 020 | NVIDIA LLM client + Fernet key + structured output contract | 5 | 019 |
| 021 | LLM throttle/backoff + queue + `org_llm_usage` metering | 5 | 020 |
| 022 | DB publish: `docs.current_content_md` + append `doc_updates` | 3 | 020 |

---

## Phase 0 — Pipeline Foundation (prerequisite, ~2 days)

**Objective:** Refactor the pipeline into discrete steps with shared storage and models.

**Tests to add first:**

- `test_first_run_creates_baseline_snapshot_no_diff`
- `test_unchanged_hash_skips_diff_and_llm`
- `test_snapshot_raw_file_written_to_configured_dir`
- `test_pipeline_selects_adapter_by_source_type`

**Deliverables:**

- `app/storage/snapshot_store.py` — write `{snapshot_id}.raw` (+ optional `.png` for scrapes)
- `DocUpdate`, `OrgLlmUsage` ORM models
- Refactor `trigger_pipeline_run` into steps:

```
fetch → normalize/hash → compare latest snapshot
  ├─ same hash → success (no-op)
  └─ changed → write snapshot → diff → [trivial? skip LLM] → LLM → publish
```

- Inject adapters via a registry keyed on `source.type`
- Extend `run_log.outcome` values: `success`, `success_no_change`, `success_trivial`, `failed`, `skipped`
- Wire APScheduler + `SQLAlchemyJobStore` so scheduled jobs call `trigger_pipeline_run`

---

## Phase 1 — Story 017: OpenAPI Adapter + oasdiff (8 pts, ~3 days)

**Objective:** Fetch OpenAPI specs, canonicalize, hash, and produce structured diffs.

**Tests to add first** (`tests/test_openapi_adapter.py`, `tests/test_openapi_diff.py`):

- `test_openapi_fetch_and_canonicalize_json_sorts_keys`
- `test_openapi_fetch_parses_yaml_to_json`
- `test_openapi_hash_is_stable_across_key_order`
- `test_oasdiff_produces_structured_changelog_in_diff_payload`
- `test_first_openapi_snapshot_has_no_diff_row`
- `test_openapi_diff_type_is_oasdiff`

**Implementation notes:**

- `app/adapters/openapi.py`:
  - HTTP fetch (reuse SSRF validation)
  - Parse JSON or YAML → canonical JSON string (sorted keys, no whitespace variance)
  - Hash SHA-256 of canonical form
  - `normalized_excerpt`: first ~2 KB of canonical JSON for DB row
- `app/diff/openapi_diff.py`:
  - Run `oasdiff changelog` (CLI or library) against two raw snapshot files
  - Parse output into JSONB → `diffs.diff_payload`
  - Set `diff_type = "oasdiff"`
- Store raw canonical spec at `raw_storage_ref`

**Fixtures:** commit small OpenAPI v2/v3 JSON/YAML pairs under `tests/fixtures/openapi/` for deterministic diff tests.

---

## Phase 2 — Story 018: Scrape/Webapp Adapter (5 pts, ~2 days)

**Objective:** Normalized HTML/text extraction, CSS scoping, snapshot persistence.

**Tests to add first** (`tests/test_scrape_adapter.py`, `tests/test_webapp_adapter.py`):

- `test_scrape_strips_script_tags_and_timestamps`
- `test_scrape_respects_css_scope_selector`
- `test_scrape_writes_screenshot_alongside_raw`
- `test_webapp_uses_manifest_fingerprint_when_available`
- `test_webapp_falls_back_to_scrape_adapter`
- `test_normalized_content_hash_is_stable_after_cosmetic_html_changes`

**Implementation notes:**

- Extract scraping from `worker_pool.py` into `app/adapters/scrape.py`
- Normalization pipeline (BeautifulSoup):
  - Remove `<script>`, `<style>`, ads/analytics patterns
  - Strip `data-*` session attributes, dynamic timestamps (regex)
  - Apply `css_scope_selector` before extraction
  - Output plain text or simplified HTML for hashing
- `app/adapters/webapp.py`:
  - Try fetching build manifest / asset fingerprint URL
  - If unavailable, delegate to scrape adapter
- Keep `ScraperWorkerPool` as concurrency wrapper; adapters stay scheduler-invoked, not in request handlers

---

## Phase 3 — Story 019: Text Diff + Triviality Heuristic (3 pts, ~1 day)

**Objective:** Unified text diff for scrape/webapp sources; skip LLM on cosmetic changes.

**Tests to add first** (`tests/test_text_diff.py`):

- `test_text_diff_produces_unified_diff_payload`
- `test_trivial_change_below_ratio_threshold_sets_is_trivial_true`
- `test_whitespace_only_change_is_trivial`
- `test_substantive_content_change_is_not_trivial`
- `test_trivial_diff_skips_llm_in_pipeline`

**Implementation notes:**

- `app/diff/text_diff.py`:
  - `difflib.unified_diff` over normalized text from consecutive snapshots
  - Triviality heuristic (tunable constants):
    - Change ratio `< 2%` of lines → trivial
    - OR only whitespace/punctuation changes
  - Store in `diffs.diff_payload` as `{ "format": "unified", "lines": [...], "change_ratio": 0.01 }`
  - Set `diff_type = "text"`, `is_trivial = True/False`

---

## Phase 4 — Story 020: NVIDIA LLM Client (5 pts, ~2 days)

**Objective:** Encrypted key storage, structured JSON output, section-only apply contract.

**Tests to add first** (`tests/test_llm_client.py`, `tests/test_key_store.py`, `tests/test_section_apply.py`):

- `test_fernet_encrypt_decrypt_roundtrip`
- `test_llm_client_never_logs_api_key`
- `test_llm_returns_structured_section_update_json`
- `test_section_apply_replaces_only_target_section`
- `test_section_apply_rejects_full_document_replacement`
- `test_llm_timeout_retries_once_on_transient_error`

**Implementation notes:**

- `app/llm/key_store.py` — Fernet with `DOCVERSION_FERNET_MASTER_KEY`
- `app/llm/client.py`:
  - OpenAI SDK, `base_url="https://integrate.api.nvidia.com/v1"`
  - Prompt enforces JSON schema: `{section_key, new_content, reason}`
  - Validate response with Pydantic before use
  - 60s timeout, retry once on 429/5xx
- `app/publish/section_apply.py`:
  - Parse `current_content_md` by heading/section key
  - Replace only the matched section — reject if model returns full doc (> N sections changed)
- Record `llm_model_used`, `token_usage` on the call (used by 021/022)

---

## Phase 5 — Story 021: LLM Throttle, Queue, Metering (5 pts, ~2 days)

**Objective:** Fair shared-key usage across orgs; no burst failures on free tier.

**Tests to add first** (`tests/test_llm_queue.py`, `tests/test_org_llm_usage.py`):

- `test_llm_queue_serializes_concurrent_calls`
- `test_llm_backoff_on_429`
- `test_org_llm_usage_increments_tokens_and_call_count`
- `test_quota_exceeded_rejects_llm_call_with_logged_error`
- `test_usage_scoped_to_current_billing_period`

**Implementation notes:**

- `app/llm/queue.py`:
  - In-process `asyncio.Queue` + semaphore (start simple; Postgres job table only if needed)
  - Global rate limiter: e.g. 10 req/min (confirm against NVIDIA published limits before merge)
  - Exponential backoff on 429
- `app/models/org_llm_usage.py`:
  - Upsert per `(org_id, period_start, period_end)`
  - Increment `tokens_used`, `call_count` after each call
  - Quota check before enqueue — return `doc_updates.status = "rejected"` if over quota (enforce in Sprint 4; hook here)

---

## Phase 6 — Story 022: DB Publish (3 pts, ~1 day)

**Objective:** Materialize doc updates; append-only audit trail.

**Tests to add first** (`tests/test_db_publish.py`):

- `test_publish_updates_docs_current_content_md_and_version`
- `test_publish_appends_doc_updates_row`
- `test_publish_links_diff_id_and_source_id`
- `test_publish_is_idempotent_on_retry_same_diff`
- `test_publish_requires_doc_target_id_on_source`

**Implementation notes:**

- `app/publish/db_publish.py`:
  - Resolve `Doc` via `source.doc_target_id` (create doc on first publish if product allows — recommend auto-create with `{source.name}` slug)
  - Apply section update via `section_apply`
  - Increment `docs.version`, set `updated_at`
  - Insert `doc_updates` row with `status="published"`
- Wire into pipeline as final step after LLM

**Integration test:**

- `tests/test_pipeline_integration.py` — mock LLM, run full cycle with fixture OpenAPI change → assert snapshot, diff, doc_update, doc version bump

---

## Sprint 3 Definition of Done

- Stories 017–022 implemented with tests
- Full pipeline: fetch → snapshot → diff → LLM → DB publish
- No LLM/Playwright in request handlers
- All new deps in `requirements.txt`
- `.bmad/sprint-status.yaml` updated: `current_sprint: 3`, stories 017–022 → `done`

---

# Sprint 4 — Publishing, Docs API, Hardening, E2E (26 pts)

**Goal:** Externally consumable docs, git mirror, ops hygiene, and confidence the system works end-to-end.

| ID | Story | Pts | Deps |
|---|---|---|---|
| 023 | Git export mirror (non-blocking failures) | 5 | 022 |
| 024 | Docs read API + custom-domain org resolution | 5 | 022 |
| 025 | Retention/cleanup job (90-day) + quota hooks | 3 | 021 |
| 026 | Security hardening pass | 5 | 010 |
| 027 | End-to-end pipeline verification + Alembic round-trip test | 8 | 022–026 |

---

## Phase 1 — Story 023: Git Export Mirror (5 pts, ~2 days)

**Tests to add first** (`tests/test_git_export.py`):

- `test_git_export_writes_file_under_org_slug_path`
- `test_git_export_updates_last_git_export_commit`
- `test_git_export_failure_does_not_rollback_db_publish`
- `test_git_export_skipped_when_disabled`

**Implementation notes:**

- `app/publish/git_export.py`:
  - Path: `{git_export_base_dir}/{org_slug}/{doc.slug}.md`
  - `git add`, `commit`, `push` to configured remote (from `docs.git_export_path` or org settings)
  - On failure: log error, leave DB publish intact
  - Update `docs.last_git_export_commit` on success
- Invoke **after** DB publish, wrapped in try/except
- Tests use temp git repo with bare remote — no network

---

## Phase 2 — Story 024: Docs Read API + Custom Domain (5 pts, ~2 days)

**Tests to add first** (`tests/test_docs_api.py`):

- `test_list_docs_scoped_to_org`
- `test_get_doc_returns_current_content_md`
- `test_get_doc_version_history_from_doc_updates`
- `test_get_diff_view_links_diff_to_doc_update`
- `test_resolve_org_by_custom_domain_when_verified`
- `test_unverified_custom_domain_not_resolved`

**Endpoints** (`app/routers/docs.py`):

| Method | Path | Notes |
|---|---|---|
| GET | `/orgs/{org_id}/docs` | List docs for org |
| GET | `/orgs/{org_id}/docs/{id}` | Content + metadata |
| GET | `/orgs/{org_id}/docs/{id}/history` | From `doc_updates` |
| GET | `/orgs/{org_id}/docs/{id}/diffs/{diff_id}` | Diff + resulting update |

- Add dependency `resolve_org(request)` — if `Host` matches verified `organizations.custom_domain`, resolve org without slug in path (for future Nginx SNI; implement lookup logic now, routing config deferred per AGENTS.md)
- Register router in `main.py`
- Also add `GET /orgs/{org_id}/sources/{id}/runs` (spec §7.1) if not present

---

## Phase 3 — Story 025: Retention + Quota Enforcement (3 pts, ~1 day)

**Tests to add first** (`tests/test_retention_job.py`, `tests/test_quota_enforcement.py`):

- `test_retention_deletes_snapshots_older_than_90_days`
- `test_retention_deletes_orphaned_raw_files`
- `test_retention_preserves_docs_indefinitely`
- `test_quota_enforcement_blocks_llm_when_over_limit`

**Implementation notes:**

- `app/jobs/retention.py` — scheduled daily job (APScheduler cron)
- Delete snapshots/diffs older than 90 days; cascade raw file cleanup
- Wire quota check in `llm/queue.py` (hook from Sprint 3) to hard-reject when `tokens_used >= token_quota`

---

## Phase 4 — Story 026: Security Hardening (5 pts, ~2 days)

**Tests to add first** (extend existing security suites):

- `test_api_responses_never_contain_nvidia_key`
- `test_error_responses_do_not_leak_stack_traces_in_production`
- `test_run_now_rate_limit_per_org_not_global`
- `test_logs_redact_secrets`
- Audit all `logger.*` calls for key/password leakage

**Checklist:**

- Redact `nvapi-*` in logging filter
- Ensure Fernet master key never in responses
- Review exception handlers — generic 500 in production
- Confirm all doc/source queries filter by authenticated org
- Rate-limit docs public endpoints if custom-domain reads are unauthenticated (decide: public read vs auth-required — spec implies public doc browsing)

---

## Phase 5 — Story 027: E2E Verification + Alembic Round-Trip (8 pts, ~3 days)

**Tests to add first** (`tests/test_e2e_pipeline.py`, `tests/test_alembic_roundtrip.py`):

- `test_e2e_openapi_source_change_triggers_publish` — full cycle with mocked HTTP + mocked LLM
- `test_e2e_scrape_source_cosmetic_change_skips_llm`
- `test_e2e_cross_org_isolation_in_pipeline`
- `test_alembic_upgrade_head_then_downgrade_minus_one`

**E2E scenario** (single test file, ~3 cases):

1. Create org, user, source (OpenAPI fixture URL), linked doc
2. Run pipeline → baseline snapshot
3. Swap fixture to changed spec → run pipeline
4. Assert: 2 snapshots, 1 diff, 1 doc_update, doc version incremented
5. Scrape source with whitespace-only change → assert `is_trivial`, no doc_update

**Alembic round-trip:** subprocess test (like `conftest.py` does for upgrade) — `upgrade head` → `downgrade -1` → `upgrade head`; verify tables intact.

---

## Sprint 4 Definition of Done

- Stories 023–027 complete
- Docs readable via API
- Git export non-blocking
- 90-day retention job registered
- E2E test passes in CI/local
- Full test suite green
- `sprint-status.yaml`: sprint 4 → `done`, backend feature-complete per plan

---

## Recommended Execution Order

1. **Sprint 3 Phase 0** — pipeline foundation + APScheduler wiring
2. **017 + 018 in parallel** — OpenAPI and scrape/webapp adapters (after Phase 0)
3. **019** — text diff + triviality (depends on 018)
4. **020 → 021 → 022** — LLM client, queue/metering, DB publish (sequential)
5. **Sprint 4 Phase 1–2 in parallel** — git export and docs API (both depend on 022)
6. **025 → 026 → 027** — retention, hardening, E2E (sequential tail)

**Parallelism note:** 017 (OpenAPI) and 018 (scrape) can proceed in parallel after Phase 0. 020–022 must be sequential.

---

## Suggested Test Files

### Sprint 3

- `tests/test_openapi_adapter.py`
- `tests/test_openapi_diff.py`
- `tests/test_scrape_adapter.py`
- `tests/test_webapp_adapter.py`
- `tests/test_text_diff.py`
- `tests/test_llm_client.py`
- `tests/test_key_store.py`
- `tests/test_section_apply.py`
- `tests/test_llm_queue.py`
- `tests/test_org_llm_usage.py`
- `tests/test_db_publish.py`
- `tests/test_pipeline_integration.py`

### Sprint 4

- `tests/test_git_export.py`
- `tests/test_docs_api.py`
- `tests/test_retention_job.py`
- `tests/test_quota_enforcement.py`
- `tests/test_e2e_pipeline.py`
- `tests/test_alembic_roundtrip.py`

---

## Risks & Open Items

| Risk | Mitigation |
|---|---|
| NVIDIA free-tier rate limits unknown | Confirm published RPM before sizing queue; default conservative (5–10 req/min) |
| `oasdiff` CLI vs Python bindings | Spike on day 1 of 017; prefer CLI subprocess if bindings immature |
| Playwright in CI | Keep httpx fallback for unit tests; mark Playwright tests `@pytest.mark.integration` |
| `doc_target_id` required before publish | Auto-create `Doc` on source create with `{source.name}` slug |
| APScheduler still stubbed | Wire in Phase 0 or Sprint 4 E2E won't test scheduled runs |
| First LLM prompt quality | Start with minimal prompt; iterate after E2E passes |

---

## Summary

| Sprint | Points | Stories | Outcome |
|---|---|---|---|
| **3** | 29 | 017–022 | Working auto-publish loop to Postgres |
| **4** | 26 | 023–027 | Git mirror, docs API, retention, hardening, E2E proof |

The highest-leverage first step is **Phase 0**: refactor `trigger_pipeline_run` into explicit steps with snapshot persistence and hash comparison. Everything in Sprint 3 plugs into that spine; Sprint 4 wraps it with delivery, ops, and verification.
