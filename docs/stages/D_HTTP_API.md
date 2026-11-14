# Stage D: HTTP API and local OpenAPI documentation

## Review range and scope

Stage: `stage-d-http-api`. Immutable base: `ba655d0fd882055af45cdd64027ade4afdf0971a`.

The user accepted C and merged PR #3 into main at `4d6dc1a4a7b3ee12723e71675cc47426530bba0a`. Published dev remained at the approved C head; its tree matched published main. The controller recorded this base on clean dev before D implementation. The explicitly deferred P3 was preserved temporarily, then restored into the candidate. Git converted line endings while applying its temporary stash; the byte-for-byte original backup restored both affected files, and only that temporary stash was removed. The unrelated reviewer-transport stash remains preserved.

D includes Pydantic request/response contracts, every required inventory/warehouse HTTP route, domain and validation error responses, database-backed health, OpenAPI, local Swagger assets, and API/snapshot regressions. The report isolation and retained-ORM correction deferred from C is included and revalidated here. The user panel, its templates/JavaScript/CSS, and final delivery material remain outside this candidate.

## Implementation decisions

- HTTP mutations call the existing service layer in one caller-owned transaction. They return HTTP 200 for an initial success and an idempotent replay, with the operation ID and current state. The response projection is built inside the transaction; FastAPI validates and encodes the committed result afterward.
- Request schemas require UUIDs and strict positive integer quantities bounded by the existing PostgreSQL limit; unknown fields are rejected. Name fields trim surrounding whitespace. Creation keys stay opaque, correcting an obsolete draft that trimmed them.
- Domain errors retain the agreed 404/409/422 codes and local shortage fields. Validation replies contain safe field paths and Russian messages without raw input/context. Database failures return a generic 503 and unexpected failures a generic 500; transaction rollback precedes the error response.
- Each HTTP report uses a request-local session and establishes REPEATABLE READ before SQL. Aggregate services additionally verify the actual PostgreSQL isolation and refresh retained ORM objects (the deferred P3). Mutation result projections remain compatible with READ COMMITTED.
- Swagger UI 5.35.0 is served entirely from `/static/vendor`; `/docs` disables its remote validator and uses local JS/CSS/favicon URLs. OpenAPI documents successful responses and the custom error schema. No product UI route or assets are included.
- The existing Swagger assets were verified byte-for-byte against the pinned npm archive and its SHA-512 integrity. Missing upstream NOTICE and bundled-license files are included. Git attributes preserve upstream bytes, including legal-file whitespace; exact SHA-256 hashes are the vendor integrity check.
- No database schema/migration or Python dependency changes are required. The architecture remains the synchronous service-backed monolith.

## Candidate files

HTTP boundary: `app/main.py`, `app/schemas.py`, `app/api/__init__.py`, `app/api/inventory.py`, `app/api/operations.py`, `app/api/warehouses.py`.

Deferred report fix: `app/services/inventory.py`, `tests/test_report_safety.py`, `docs/stages/D_REPORT_SAFETY.md`.

HTTP checks: `tests/test_api.py`, `tests/test_report_snapshot.py`, `tests/test_openapi.py`, `tests/test_http_errors.py`.

Local documentation assets: `app/static/vendor/swagger-ui-bundle.js`, `swagger-ui.css`, `favicon-32x32.png`, `LICENSE`, `NOTICE`, `swagger-ui-bundle.js.LICENSE.txt`.

Documentation and Git handling: `.gitattributes`, `README.md`, `docs/DECISIONS.md`, `docs/vendor-notice.md`, this document.

Only this explicit allowlist is overlaid on the recorded base for candidate builds, tests and eventual review. No untracked E/F drafts, private runtime settings or original backups enter the image or review inputs.

## Verification evidence

The 24-file candidate was exported through a temporary Git index at tree `06de4356dca46843f476132808c6e55248524cb8`. The real index remained empty. Only this stage evidence/wording was updated after the run; application, tests, vendor assets and build configuration remain identical to the verified export.

Runtime checks used the new isolated Compose project `checkstock_stage_d_20261114`, loopback port 18085 and a fresh named PostgreSQL volume. Private runtime settings were generated outside the export and never printed or added to the image/review package. Existing demo, B, C and deferred-P3 projects were preserved. Every Compose command specified the isolated project name, external settings file, project directory and Compose file.

| Command/check | Observed result |
| --- | --- |
| Fetch published dev/main, inspect PR #3 and compare branch trees | PR #3 merged; published branch trees identical at the merge/base recorded above. |
| `python scripts/review/local_review.py start --stage stage-d-http-api` | STARTED at `ba655d0fd882055af45cdd64027ade4afdf0971a` before D edits; deferred P3 restored afterward. |
| `git diff --cached --check` through the temporary candidate index | Passed for the allowlisted candidate. Upstream vendor whitespace is preserved by attributes and verified through raw hashes. |
| `docker compose config -q` | Passed without printing resolved settings. |
| `docker compose up --build -d --wait --wait-timeout 180` | Image built successfully; application and PostgreSQL healthy. Unchanged dependency installation reused its build layer. |
| Inspect PostgreSQL version/timezone | PostgreSQL 17.12, Etc/UTC. |
| Read all five application tables before seed | Empty; startup/migrations did not create demonstration rows. |
| `docker compose exec -T app python -m app.seed`, twice | 25 physical units, no reserve/transit, six logical cells and four receipts/events; complete snapshots including IDs/timestamps unchanged by the repeat. |
| `docker compose exec -T app pytest -q` | **353 passed in 40.20s**: 250 existing C cases, 18 deferred P3 regressions and 85 HTTP/OpenAPI/snapshot/error cases, using real PostgreSQL. |
| `docker compose exec -T app alembic check` | `No new upgrade operations detected.` |
| Dependency checks inside the full pytest run | All 29 installed runtime/test pins matched; the dependency test's `pip check` passed. No dependency update was made. |
| Browser verification at `/docs` | Swagger rendered all required route groups. `GET /api/stocks` through Try it out → Execute returned HTTP 200, six cells, 25 physical units and zero reserves/transits. This was a read-only request. |
| Local Swagger/HTTP checks in the suite | All required paths and custom error schemas documented; local JS/CSS/favicon served with appropriate types; all three license/notice files served; HTML references only local assets and disables the external validator. |
| Compare all demo rows after tests and browser check; query temporary schemas | Every demo row unchanged; no disposable test schemas remained. |
| Compare running-image source/configuration and vendor files with the export | Exact match after normalizing Python/configuration line endings; vendor bytes match without normalization. No E templates, app.js/app.css or web tests are present. |
| Verify six vendor files against the documented upstream SHA-256 values | All match; the import also verified the npm archive SHA-512 and corresponding archive members. |

The HTTP suite exercises all required command lifecycles and read models, exact stock/reserve/transit counts, a draft's immediate event and replay, opaque keys and conflicts, strict payloads/UUIDs, unsafe Unicode and malformed bodies, local shortage and reserve protection, archive/rename rules, unknown/wrong-kind operations, rollback after flushed mutations, sanitized 503/500 replies, and concurrent HTTP requests on independent PostgreSQL connections. Snapshot tests inspect the actual isolation of all four report routes and overlap both stock and operation reports with separately committed changes. The deferred tests additionally protect against stale ORM instances retained across transactions.

No application test failed. The initial browser locator used accessibility spacing rather than the DOM name; inspecting the DOM resolved it and the request succeeded. This was an automation-selector correction, not an application defect. The only preparation issue was Git line-ending conversion during deferred-patch restoration, resolved from the byte-preserved backup before implementation. No schema or architecture deviation was needed. Local Swagger assets/licensing and the explicitly deferred P3 are the documented additions within D scope.

Review-controller tests were not rerun because that subsystem was not modified. No independent Claude approval is claimed for this uncommitted candidate. Product-panel checks and final clean-start/restart acceptance remain E/F work; historical B/C evidence is not substituted for the fresh D run above.

## Handoff

Initial stage commit, publication and independent Claude review require the user's approval of the concrete tested D candidate. No D review result is claimed before that workflow. Stage E begins only after the subsequent user-owned main integration and authorization.
