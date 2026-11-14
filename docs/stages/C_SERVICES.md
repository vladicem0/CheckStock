# Stage C: inventory services and transactions

## Review range and scope

Stage: `stage-c-inventory-services`. Immutable base: `cf839f7da49cf8edc74b79c6efacdb62f6a46dec`.

The user accepted stage B and merged PR #2 into `main` at `e32d13d4b536814e1d617d800028c57ea1006a32`. Published `dev` and `main` had identical trees at stage start; the difference was the merge commit. The controller recorded published `dev` as the base before any implementation edits.

Stage C adds transfer, FBS and FBO commands, warehouse creation/rename/archive, and stock/transit/operation/history read models. The existing receipt service supplies shared validation, idempotency and locking primitives. Application schema, migrations, dependency pins, Compose and the health-only entry point are unchanged. Business HTTP routes, Pydantic request schemas, OpenAPI, UI and final demonstration material remain in D-F.

## Decisions and corrections

- Each command runs in the caller's transaction. Creation locks its idempotency key; transitions lock the operation. Commands then lock warehouse rows in UUID order and the selected balance, refreshing cached ORM state. No service commits independently.
- Availability is local to the selected warehouse. Transfer/FBO cannot spend another order's FBS reserve; FBS shipment consumes its own quantity and preserves other reservations. A shortage reports `available_here` and `requested` without using another warehouse.
- Transfer creation records exactly one `transfer_created` event, including the draft status and historical route names, without touching balances or transit. Dispatch checks current availability. Receive credits only the destination; overflow leaves the operation in transit. The deferred stage A history clarification is implemented here.
- Replaying creation returns the original operation before checking current warehouse archive state. Replaying an executed transition returns the current state without another event, including dispatch after receipt. Opposite FBS terminal transitions remain conflicts.
- Archive preserves balances, identifiers and history. New incoming operations are forbidden; outgoing transfers to active warehouses and completion of earlier operations remain allowed. Rename affects future event snapshots, not existing history.
- The imported draft referenced a missing local-availability helper and returned 409 for same-warehouse transfers. The helper is supplied and identical source/destination now raises 422 before SQL, as required by the accepted contract.
- Text fields reject NUL and unpaired Unicode surrogates before SQL, returning a domain 422 instead of an encoding/driver exception. Warehouse/customer/marketplace names are trimmed; idempotency keys remain opaque strings. This validates existing fields without adding a business feature.
- Read models include every product/warehouse cell, zeros and archived rows, and derive the three transit categories solely from operation statuses. Consistent multi-query reports require a fresh caller-owned REPEATABLE READ transaction established before the first SQL statement. HTTP wiring belongs to D; command transactions use READ COMMITTED and locks.

## Candidate files

Services: `app/services/stock.py`, `app/services/operations.py`, `app/services/warehouses.py`, `app/services/inventory.py`.

Tests: `tests/helpers.py`, `tests/test_lifecycles.py`, `tests/test_idempotency_and_rollback.py`, `tests/test_concurrency.py`, `tests/test_warehouses.py`, `tests/test_inventory.py`.

Documentation: `README.md`, `docs/DECISIONS.md`, `docs/stages/C_SERVICES.md`.

Only this allowlist is added to the committed base for verification. Existing untracked D-F drafts and the separately preserved reviewer-transport edits are excluded. No changes to the external reviewer policy or review controller are part of C.

## Verification evidence

The candidate was exported from a temporary Git index at tree `ef797c3678b87b01259098462e20bac57cc17dad`, leaving the real index unchanged. The verification prose in this section was added after execution; application, test and build content are identical to the tested export. Original drafts were preserved before editing.

Checks used the new isolated Compose project `checkstock_stage_c_20261114`, its own fresh PostgreSQL volume and loopback port 18083. Existing demo and B verification projects were preserved. Private runtime settings were generated outside the candidate and were neither printed nor included in the image or review inputs. Every Compose command used explicit `-p`, `--env-file`, `--project-directory` and `-f` arguments pointing to that isolated export.

| Command/check | Observed result |
| --- | --- |
| `git fetch origin dev main`, inspect PR #2 and compare published branch trees | PR #2 merged; published main/dev trees identical, with the merge SHA and immutable dev base recorded above. |
| `python scripts/review/local_review.py start --stage stage-c-inventory-services` | STARTED before implementation edits at the immutable base above. |
| `git diff --cached --check` using the temporary candidate index | Passed; only the 13-file allowlist differs from the base, and the real index remains empty. |
| `docker compose config -q` | Passed without printing resolved settings. |
| `docker compose up --build -d --wait --wait-timeout 180` | Candidate image built; application and PostgreSQL healthy. Unchanged dependency installation reused its existing build layer. |
| Inspect PostgreSQL version/timezone | PostgreSQL 17.12; Etc/UTC. |
| Read all five application tables before explicit seed | Empty; migration/startup created no demo rows. |
| `docker compose exec -T app python -m app.seed`, twice | 25 physical units, no reserves/transit, six logical cells, four receipts/events; complete row snapshots including IDs and timestamps identical after repeat. |
| `docker compose exec -T app pytest -q` | **250 passed in 25.56s**, using real PostgreSQL. Includes all 84 existing B cases and 166 C cases. No failed tests. |
| `docker compose exec -T app alembic check` | `No new upgrade operations detected.` |
| `docker compose exec -T app python -m pip check` | `No broken requirements found.` The suite also verifies the complete set of 29 pinned runtime/test distributions. |
| Compare all demo rows after tests; query disposable test schemas | Every demo row unchanged; no `test_checkstock_...` schemas remained. |
| Compare exported source/configuration hashes with the running image, normalizing CRLF to LF | All 30 copied Python/configuration entries matched. No future-stage source or tests entered the image. |
| Inspect process UID, routes and filesystem | UID 10001; only `/health`; no business API, schemas, templates, static files, HTTP/UI/snapshot tests or review-controller tests. |
| `GET http://127.0.0.1:18083/health` | HTTP 200, `{"status":"ok"}`. |

The following acceptance coverage is included in that single full application run:

| Scenarios | Evidence |
| --- | --- |
| T01/T10, receipt regressions | Existing seed/receipt suites plus new malformed-key checks; idempotent seed, strict quantities, local receipts, key conflicts and atomic rollback. |
| T02-T07 | `test_lifecycles.py`: exact physical/reserved/transit counts, every replay, forbidden terminal transitions, local shortage, reserve protection, dispatch recheck, transfer receipt overflow and separate FBS orders. |
| T08/T09 | `test_warehouses.py`: archive preserves all inventory/history, outgoing evacuation and earlier operations remain possible, new operations are rejected, rename preserves event snapshots, invalid names/IDs and caller rollback. |
| T10/T12/T13 | `test_idempotency_and_rollback.py`: normalized identity, conflicting payload/kind/route, replay after archive and terminal state, injected failure after flushed mutations for all eight new command paths, one complete event per successful action. |
| T11 and concurrent retries | `test_concurrency.py`: distinct PostgreSQL backend connections, over-reservation race, FBS versus FBO competition, identical/conflicting creates, duplicate transitions, ship versus cancel, opposite transfers, stale ORM refresh, and observed database lock waits during archive/rename. |
| R01/R02/R10 read models | `test_inventory.py`: full zero/archived matrix, three simultaneous transit categories, conservation and received-state filtering, current operation names versus historical snapshots, concurrent shipment between balance/transit reads within caller-owned REPEATABLE READ. |

No schema, dependency or architecture expansion was needed. Corrections reconcile the old service drafts with the accepted contract and validate PostgreSQL-compatible text. The controller and reviewer policy were unchanged, so controller tests were not rerun or counted as application evidence. HTTP/UI tests and a new restart durability exercise were not performed in C; restart infrastructure is unchanged from B, while API/UI acceptance remains scheduled for D/E/F. Independent Claude review is not claimed for this uncommitted candidate.

## Acceptance boundary

T01-T13 are exercised through services and real PostgreSQL. The tested 404/409/422 values are domain error codes; HTTP response mapping and request UUID validation are D responsibilities. T14's visible interface and final delivery checks remain for E/F. The C candidate must be approved by the user before commit, publication to dev and independent Claude review. Main integration remains the user's responsibility.
