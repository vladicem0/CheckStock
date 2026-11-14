# Deferred Stage D P3 corrections

Stage E inclusion: after PR #4 was merged, the user authorized both corrections
as part of `stage-e-web-panel`. Its recorded base is the approved D head below.
The patch was preserved while recording the clean base, then restored exactly.
The historical standalone verification below remains evidence of the earlier
patch; fresh combined verification belongs in `E_WEB_PANEL.md`.

## Scope and approval boundary

The Stage D review approved base `ba655d0fd882055af45cdd64027ade4afdf0971a` through head `6c6652253e24c1b5437d8e3daeba12be87afbdbf` with two nonblocking P3 findings. The user requested local corrections and deferred their independent review to the next stage. These corrections remain uncommitted, are excluded from the Stage D PR, and are not covered by D's APPROVED result.

The future Stage E candidate must include this preserved patch, its tests and the decision update before approval and review. Preserve it while establishing the required clean, published Stage E base; do not reset the Stage D base or reopen its approval for this work. This preparation does not begin the product-panel implementation.

## Changes

- `app/services/inventory.py`: project operations from bulk-refreshed product and warehouse maps. The operation list uses three SELECTs plus the isolation check; inventory uses its existing catalogs, balances and transit query plus the isolation check. Neither report adds a query for each operation. REPEATABLE READ/SERIALIZABLE checks and refresh of retained ORM objects remain enforced. Single-command projections keep their existing refresh behavior.
- `app/main.py`: log SQLAlchemy failures with the exception class, response status and traceback frame locations. Do not log SQL, bound parameters, driver messages, frame source/locals or request bodies. OperationalError, InterfaceError, pool TimeoutError and an invalidated DBAPI connection return the sanitized 503; other SQLAlchemy errors return the sanitized 500. Health retains its readiness 503 and now logs failures.
- `tests/test_report_safety.py`: add two regression cases comparing query counts before and after 30 mixed FF/FBS/FBO shipments. Verify report content and unchanged data as well as the constant 4/5 statement budgets.
- `tests/test_http_errors.py`: add eight exception-classification/logging/rollback cases and one actual PostgreSQL CHECK-violation case. Assert diagnostic markers never reach clients or logs.
- `tests/test_health.py`: extend the existing readiness failure check to cover safe logging.
- `docs/DECISIONS.md`: document the deferred fixes and their scope.

Database schema, dependencies, transaction ownership and stock rules are unchanged. The error classification is the optional improvement recommended in the same P3 finding; it adds no domain feature.

## Verification on 2026-11-15 (Asia/Omsk)

A temporary Git index exported published D plus only the six modified files listed above at tree `b6a5e63e99dd01be0c85fd14000182db245ca9b6`. The real index stayed empty. This evidence document was added afterward and does not alter tested application code.

The isolated Compose project was `checkstock_d_p3_20261115`, with a fresh named PostgreSQL volume and loopback port 18086. Runtime settings were generated outside the candidate, never printed or included in the build. Existing working/demo projects were preserved. Untracked product-panel drafts, final-delivery documents and web tests were excluded from the export and image.

| Command/check | Actual result |
| --- | --- |
| `git diff --check` | Passed. |
| `docker compose config -q` | Passed. |
| `docker compose up --build -d --wait --wait-timeout 180` | Built successfully; application and PostgreSQL healthy. |
| Read application tables before tests | All five tables empty; no seed was requested for this validation project. |
| `docker compose exec -T app pytest -q` | **364 passed in 41.35s** on real PostgreSQL: the 353 D cases plus 11 new P3 regressions. |
| `docker compose exec -T app alembic check` | No new upgrade operations detected. |
| Read application tables and temporary schemas after tests | All five public application tables still empty; no disposable test schemas remained. |
| Compare image source, tests, configuration and vendor files with the export | Matched; Python/configuration line endings normalized, vendor bytes compared exactly. No E templates, panel assets or web tests present. |
| Compare all six modified files with the workspace | Matched the tested export after line-ending normalization. |

The suite retained stale-ORM, concurrent report snapshots, lifecycle, idempotency, local availability, rollback and concurrent-command coverage. No tests failed. Reviewer-controller tests were not rerun because that subsystem was not changed. No UI workflow changed or required a new browser run. Independent Claude review of these corrections remains deferred to Stage E; D's original 353-test evidence remains the applicable evidence for its unchanged PR.
