# Deferred report safety correction for stage D

## Authorization and scope

Stage C was approved at `ba655d0fd882055af45cdd64027ade4afdf0971a`, iteration 0, with one optional P3 about read-model isolation and retained ORM state. The user explicitly requested implementing this correction now, leaving it uncommitted for D, and opening the C pull request from the already approved dev head. This work is not a new C review round or the start of full stage D.

Files: `app/services/inventory.py`, `tests/test_report_safety.py`, `docs/DECISIONS.md`, and this evidence document. No schema, dependency, HTTP or UI changes are included.

## Behavior

- `get_inventory`, `list_operations` and `list_history` check the actual PostgreSQL transaction isolation before reading report data. REPEATABLE READ and SERIALIZABLE are accepted; weaker isolation raises an explicit RuntimeError for caller-contract misuse.
- Report queries refresh existing ORM objects, including product and warehouse references used in operation projections. This prevents stale state retained by `expire_on_commit=False` from contaminating a later report snapshot.
- Transaction ownership remains with the caller. The guard neither changes isolation nor commits/rolls back. Stage D HTTP reports must establish REPEATABLE READ before their first SQL statement, preferably in a fresh session.
- The projection of an individual command result remains usable in its READ COMMITTED write transaction.

## Verification

The four-file correction was exported from a temporary Git index over approved C HEAD, with tested tree `9d45817dc11ae8814237458760a448ce1d68c07d`. Only this verification prose was added afterward; runtime and test files remain identical. The real Git index stays empty and no commit, push or independent review is performed for this deferred correction.

Checks used a new isolated Compose project `checkstock_report_safety_20261114`, loopback port 18084 and a fresh PostgreSQL volume. Runtime settings were generated outside the export without printing their contents. Existing demo/C/B projects were preserved. Every Compose invocation specified this project's name, configuration file, project directory and external runtime settings.

| Command/check | Observed result |
| --- | --- |
| `git diff --cached --check` with the temporary index | Passed for the four-file allowlist; real index remained empty. |
| `docker compose config -q`; `docker compose up --build -d --wait --wait-timeout 180` | Configuration valid; application and PostgreSQL healthy. |
| Empty application-table check, explicit `python -m app.seed` | Initially empty; seed produced 25 physical units, no reserve/transit and four receipts/events. |
| `docker compose exec -T app pytest -q` | **268 passed in 27.38s**, on real PostgreSQL: the existing 250 cases and 18 new report-safety cases. |
| `docker compose exec -T app alembic check` | `No new upgrade operations detected.` |
| `docker compose exec -T app python -m pip check` | `No broken requirements found.` |
| Full demo snapshot comparison and disposable-schema check after tests | All demo rows unchanged; no test schemas remained. |
| Compare normalized source/configuration hashes between candidate and running image | Exact match; untracked HTTP/UI drafts were excluded. |
| `GET http://127.0.0.1:18084/health` | HTTP 200, `{"status":"ok"}`. |

The new cases reject READ COMMITTED/READ UNCOMMITTED for all three report entrypoints, accept both supported isolation levels, detect an active transaction changed independently of the connection configuration, refresh retained balance/operation/product/warehouse objects across commits, and preserve normal READ COMMITTED command-result projection. The existing concurrent-shipment snapshot test also passed. No failed tests were observed. Full stage D and its HTTP integration are not claimed as verified by this service-only run.

## Stage D handoff

Keep this correction out of the C commit, review and pull request. Before the formal D start, preserve these local changes, record D's immutable base on clean dev matching published dev with the review controller, and then restore the correction into the D candidate. Do not rewrite the C approval or its base. Include these regressions and HTTP snapshot/error-boundary checks when validating D; existing later-stage drafts are not evidence of compatibility.

## Incorporation into D

After PR #3 was merged and continuation authorized, `stage-d-http-api` recorded immutable base `ba655d0fd882055af45cdd64027ade4afdf0971a` on clean dev. This correction was restored into D after that start. The verification above remains evidence of the earlier isolated correction, not a claim that the later HTTP candidate passed. Current API integration, snapshot checks and verification results are recorded in [D_HTTP_API.md](D_HTTP_API.md).
