# Sprint 2 implementation plan (TDD-first)

## Goal

Complete Sprint 2 stories 010-016 and close the likely bug classes that appear while building tenancy-aware CRUD, source management, manual runs, scheduler orchestration, and Playwright-based scraping.

This plan follows the authoritative contract in `design_spec.md` and uses test-driven development for every slice of work.

## Scope

### Stories to implement
- Story 010: resolve org from JWT/session, enforce org-scoped access, and block insufficient roles.
- Story 011: org invite and membership management for owners/admins.
- Story 012: sources CRUD endpoints restricted to the current org.
- Story 013: manual `run-now` endpoint with per-org rate limiting and input validation.
- Story 014: APScheduler + SQLAlchemy job store with one job per active source.
- Story 015: per-source lock, `run_logs` recording, and a pipeline skeleton.
- Story 016: Playwright worker pool, concurrency cap, and SSRF validation.

## Guiding principles

- Never trust `org_id` from the client. Derive it from the authenticated context and enforce scoping in the ORM/query layer.
- Treat tenancy as the highest-risk bug class. Cross-tenant leakage must be prevented before any CRUD endpoint is considered complete.
- For every feature, write the failing test first, implement the smallest fix, run the relevant test slice, then refactor.
- Keep the scheduler and Playwright worker separate from request handlers; API handlers should only enqueue or trigger, not do scraping/LLM work inline.

## TDD workflow for each slice

For every story or bug fix:
1. Add or update a regression test in `tests/`.
2. Run the targeted test(s) and confirm they fail.
3. Implement the minimal production change.
4. Re-run the targeted test(s).
5. Run the broader related suite (for example `tests/test_auth_dependencies.py`, `tests/test_membership_service.py`, or the new sources/scheduler suites).
6. Only then refactor for clarity or reuse.

## Implementation sequence

### Phase 1 — Lock down the tenancy contract

Objective: make org scoping and role enforcement battle-tested before adding more surface area.

Tests to add first:
- `test_get_current_org_rejects_access_for_non_member`
- `test_get_current_org_rejects_cross_org_access_even_if_client_supplies_other_org_id`
- `test_require_role_returns_403_for_insufficient_privilege`
- `test_membership_management_is_org_scoped`

Deliverables:
- Keep the existing JWT/org dependency behavior intact.
- Add regression tests for cross-tenant leakage and role enforcement.
- Verify that all membership and access checks remain scoped to the authenticated org context.

### Phase 2 — Sources domain and API

Objective: add source persistence and CRUD endpoints for the current org.

Tests to add first:
- `test_sources_list_only_returns_current_org_sources`
- `test_sources_create_is_scoped_to_current_org`
- `test_sources_update_rejects_cross_org_modification`
- `test_sources_delete_requires_admin_or_owner`
- `test_sources_create_rejects_invalid_type_or_url`

Implementation notes:
- Add a `Source` model with fields such as `org_id`, `name`, `source_type`, `target_url`, `css_scope_selector`, `fetch_interval_seconds`, `is_active`, and timestamps.
- Add schemas for create/list/update responses.
- Add a router under `app/routers/` for `/orgs/{org_id}/sources`.
- Ensure the service layer uses the authenticated org context and not client-supplied `org_id` values.

### Phase 3 — Manual run endpoint and throttling

Objective: make manual runs safe and rate-limited per org.

Tests to add first:
- `test_run_now_rejects_invalid_source_id_or_missing_source`
- `test_run_now_uses_current_org_scope_and_ignores_client_org_id`
- `test_run_now_rate_limited_per_org`
- `test_run_now_rejects_invalid_target_url_before_enqueue`

Implementation notes:
- Add a `POST /orgs/{org_id}/sources/{source_id}/run-now` endpoint.
- Validate the source belongs to the org and is active.
- Reuse the existing rate-limit infrastructure where possible, but ensure the limit is scoped by org and not by user only.
- Enqueue work rather than doing pipeline work inline in the request handler.

### Phase 4 — Scheduler core

Objective: create a scheduler process skeleton that can register one job per active source.

Tests to add first:
- `test_scheduler_registers_one_job_per_active_source`
- `test_scheduler_skips_inactive_sources`
- `test_scheduler_does_not_duplicate_jobs_on_restart_or_reconfigure`
- `test_scheduler_uses_sqlalchemy_job_store`

Implementation notes:
- Introduce a scheduler service module (for example `app/scheduler/`) with a small public API such as `start_scheduler()`, `register_source_jobs()`, and `trigger_source_run()`.
- Use APScheduler with SQLAlchemy job store, but keep the implementation behind an abstraction so tests can verify behavior without needing a live scheduler loop.
- Make the scheduler process separate from request handlers.

### Phase 5 — Run locking, run logs, and pipeline skeleton

Objective: prevent concurrent runs of the same source and preserve a trace of each attempt.

Tests to add first:
- `test_parallel_run_now_requests_do_not_overlap_for_same_source`
- `test_run_logs_are_written_on_success_and_failure`
- `test_pipeline_skeleton_records_status_transition_for_source_run`

Implementation notes:
- Add a `RunLog` model to capture start/end timestamps, status, error message, and source correlation.
- Enforce a per-source lock so only one run executes at a time.
- Record a log row for every invocation, including failure cases.

### Phase 6 — Playwright worker pool and SSRF hardening

Objective: make scraping safe and bounded.

Tests to add first:
- `test_ssrf_blocks_private_ip_ranges_and_link_local_addresses`
- `test_ssrf_blocks_metadata_endpoint_169_254_169_254`
- `test_playwright_worker_pool_respects_concurrency_limit`
- `test_invalid_url_is_rejected_before_worker_launch`

Implementation notes:
- Add a URL validation helper shared by the scheduler and any worker entrypoint.
- Block RFC1918 ranges, link-local ranges, and the metadata endpoint.
- Implement a semaphore-based worker pool with a conservative concurrency cap (for example 3-5).
- Ensure the worker pool is invoked from the scheduler, not from API handlers.

## Bug classes to explicitly guard against

The following regressions should be covered by regression tests before code lands:
- Cross-tenant data access caused by missing org scoping.
- Duplicate scheduler jobs or overlapping runs for the same source.
- Manual run endpoint bypassing validation or rate limits.
- SSRF via private IPs, link-local IPs, or the metadata endpoint.
- Missing `run_logs` rows when a run fails.
- Inconsistent role enforcement for owners/admins vs members/viewers.
- Transaction rollback errors that leave partially created memberships or sources.

## Suggested test files

- `tests/test_sources_api.py`
- `tests/test_sources_service.py`
- `tests/test_run_now_api.py`
- `tests/test_scheduler_service.py`
- `tests/test_run_logs.py`
- `tests/test_ssrf_validation.py`
- `tests/test_playwright_pool.py`

## Definition of done for Sprint 2

The sprint is complete when:
- All Sprint 2 stories 010-016 are implemented and covered by tests.
- The full test suite passes.
- No endpoint can leak data across orgs.
- Manual runs and scheduler runs are safe under concurrency and rate limits.
- Playwright-based scraping is blocked from SSRF targets.
- The codebase is ready for Sprint 3 adapters, diffing, and LLM integration.

## Recommended execution order

1. Add tenancy regression tests and stabilize the existing auth/membership foundation.
2. Implement source CRUD and scoped API access.
3. Implement `run-now` and rate limiting.
4. Implement scheduler registration and per-source locking.
5. Add worker-pool and SSRF protections.
6. Run the full suite and fix any regressions uncovered by new tests.
