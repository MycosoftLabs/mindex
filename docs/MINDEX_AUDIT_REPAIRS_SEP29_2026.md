# MINDEX audit repairs — September 29, 2026

Status: locally validated, not deployed. Branch: `codex/mindex-audit-repairs`. Base: `b1137e9` (local Sep23 main, FormSpace merge). Independent local clone under the managed `system-audit-repairs` workspace; original repositories and deployed services were not changed. No commit or push was performed.

This batch addresses findings M-01 and M-02 in the read-only audit saved at `C:/Users/Owner1/Documents/Codex/2026-09-29/realtime-voice-chat/outputs/mindex-mycorrhizae-audit.md`. The audit and repair plan were written before source edits.

## Changes

| File | Finding | Before | After |
|---|---|---|---|
| `mindex_api/routers/worldview/search.py` | M-01 | Public search called internal search with unsupported `domains`, `radius_km`, `db` arguments and treated domain-keyed results as a flat list. | Calls the actual `types`, `radius`, `session` contract with explicit scalar defaults; flattens allowed buckets; retains existing row domain or supplies the bucket domain; strips internal rows; applies the public total limit. |
| Same adapter | M-01 | An unsupported/internal-only domain selection could become an unrestricted search. | Explicit safe domain list for defaults and mixed requests; HTTP 400 when no allowed domain remains. |
| `mindex_api/middleware/metering.py` | M-02 | SQLAlchemy parsed PostgreSQL shorthand parameter casts into truncated names (`key_i`, `i`, `met`), preventing the usage transaction. | Three statements use SQL-standard `CAST(:parameter AS type)` for UUID, inet and JSONB. |
| `tests/test_worldview_search_contract.py` | M-01 regression coverage | No signature/DTO integration coverage for this adapter. | Seven cases cover the actual service signature, model/dict buckets, private bucket filtering, invalid domains, region/limit forwarding, default public domains, total limit and empty results. |
| `tests/test_worldview_metering_contract.py` | M-02 regression coverage | No PostgreSQL parameter-binding transaction coverage. | Two cases compile and construct actual PostgreSQL/asyncpg parameters for all three statements, assert commit, and verify rollback on storage failure. |
| This file | Delivery record | No audit-linked repair record. | Scope, behavior, validation, remaining risks and rollback recorded. |

## Validation

- Before fixes, new regression tests: 8 failed, 1 passed. Failures reproduce the adapter mismatch and unbound billing parameters; the rollback case already passed.
- After fixes, new regression tests plus existing `test_api_routes.py` and `test_api_contract_openapi.py`: 15 passed. Existing warnings remain for Pydantic deprecations and duplicate library-catalog OpenAPI operation IDs.
- `git diff --check` passes for the repair scope.
- Runtime: Python 3.12, pytest 8.4.2, pytest-asyncio 0.23.8, SQLAlchemy 2.0.49, FastAPI 0.111.1, Pydantic 2.13.3, existing installed dependencies. No dependency installation or build.
- Execution used scratch cwd and pytest basetemp, `-B`, disabled pytest cache/autoload, and an explicit asyncio plugin. The Python audit hook rejects `socket.connect` and `socket.sendto`, except the socket module's Windows `_fallback_socketpair` connection used by asyncio. It rejects `open` events for string/bytes paths whose lowercased basename is exactly `.env` or `.credentials.local`; `agent.env` is not on this harness's list. This is not a general credential-read sandbox: other filenames, inherited environment values and access outside those audit events are not covered. Database/auth/governance are test fixtures; the inspected tests load no production credential files, and no production database access or usage writes occurred.
- Reproduction and logs: audit `work/mindex-audit/reproduced-defects.json`, `repair-tests-before.txt`, `repair-tests-after.txt`, and `run_isolated.py`.

Four checked-in generated UTF8 test log files appear modified immediately after clean clone/checkout, matching other local worktrees. They were not edited for this repair and are excluded from the scoped patch.

## Remaining limits

These changes do not claim production readiness. M-03 quota ordering, M-04 entitlement enforcement, M-05 durable Mycorrhizae routing, M-06 NLM consumer routes, M-07 search cache filters, M-08 search session/error/live-acquisition semantics, M-09 ETL outcome/freshness reporting, and later audit items remain open. In particular, fixing public search exposes the existing internal search path; that path's cache/concurrency/live-scrape behavior must be corrected before promising customer correctness or read-only acquisition semantics. Metering still uses a fire-and-forget task and only records requests with identity in state. No real PostgreSQL execution, load test, customer billing transaction, deployment, or service restart was performed.

Acceptance before release: replay the repaired usage transaction against a disposable PostgreSQL instance with current migrations; run a customer-key staging search with governance and source-domain fixtures; resolve quota/entitlement/cache gaps; verify deployment SHA. Deployment is outside this batch.

Rollback: revert only the two changed implementation files, two new test files and this delivery note in the isolated branch, or discard the scoped patch. Keep unrelated generated logs untouched. Nothing is deployed, so no production rollback action is needed.
