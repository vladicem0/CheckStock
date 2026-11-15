# FBS delivery confirmation

## Scope

The user explicitly requested the optional FBS delivery confirmation after accepting stages A–F. This implements the P1 extension in section 3.5 of CHECKSTOCK_CODEX_PLAN.md. The immutable stage base is 38805902c8a467c6a1af3c07587d5f7b6ac68be5; stage slug: fbs-delivery.

- Add the FBS-only transition shipped -> delivered and one fbs_delivered history event.
- Add POST /api/fbs/{operation_id}/deliver and a Russian confirmation button in the existing operation journal.
- Remove delivered shipments from FBS transit without changing physical stock or reservations.
- Preserve replay semantics, transaction boundaries, concurrent safety, archive/rename behavior and existing data.
- Add a forward migration instead of editing the initial migration. Refuse downgrade while delivered operations exist.
- Reconcile README, acceptance criteria and the personal demonstration script with the new optional transition. Preserve pending cross-platform setup and PowerShell documentation edits.

No carrier integration, partial deliveries, returns, new dependencies or architecture changes are included.

## Required checks

- Real PostgreSQL lifecycle, replay, invalid-state, rollback and concurrency tests.
- Upgrade a populated initial schema without changing rows; validate constraints, safe downgrade refusal and an empty-schema migration round trip.
- HTTP status codes, serialization and OpenAPI coverage.
- Browser button visibility, successful completion, refresh and safe retry behavior.
- A visible workflow against a separate real PostgreSQL database, including repeat confirmation and unchanged stock.
- Full application pytest, alembic check, pip check and browser suite on the exact candidate.
- Verify published dev/main and report which pending documentation changes are absent from main.

Implementation approval, commit, dev publication and independent Claude review remain separate handoffs under AGENTS.md. No review approval is claimed by this scope document. Verification results will be recorded after the commands actually run.

## Candidate files

| Files | Change |
| --- | --- |
| app/models.py, app/schemas.py | FBS-only delivered database status and HTTP serialization. |
| alembic/versions/0002_fbs_delivery.py | Forward CHECK-constraint migration, guarded downgrade. The initial migration is unchanged. |
| app/services/operations.py, app/api/operations.py | Transactional delivery command and POST endpoint. |
| app/static/app.js, app/templates/index.html | Delivery button, delivered status/event labels and accurate transit explanation. |
| tests/test_fbs_delivery.py, tests/test_migrations.py, tests/test_constraints.py | Real database lifecycle, migration, replay, rollback, locking and constraint checks. |
| tests/test_api.py, tests/test_openapi.py | HTTP lifecycle/error/rollback/concurrency and API-schema checks. |
| tests/browser/panel.spec.js | Five additional browser cases and updated shipped-state expectations. |
| README.md, tests/browser/README.md | Preserve pending first-run order, port 8000 and session-scoped PowerShell instructions; describe the new FBS command in README. |
| DEMO_SCRIPT.md | Preserve the pending personal script and add delivery confirmation; its final state now has 15 events and zero FBS transit. |
| docs/DECISIONS.md, docs/ACCEPTANCE.md, CLAUDE.md | Record the authorized extension and its invariants in domain, acceptance and reviewer context. |
| docs/VERIFICATION.md, docs/stages/FBS_DELIVERY.md | Preserve historical F verification and identify the current extension evidence. |

The architecture, runtime dependencies and review controller are unchanged. The feature is an explicitly requested P1 option from the original plan, not an additional marketplace integration.

## Verification performed on 2026-11-15

An isolated candidate was exported from all tracked files plus only the three authorized new stage files. Its source hashes were checked against the working tree before updating the local application. The separate Compose project was checkstock_fbs_delivery_20261115, port 18093, with a new PostgreSQL volume. No working database was reset or seeded during this implementation.

| Actual command or check | Observed result |
| --- | --- |
| python scripts/review/local_review.py start --stage fbs-delivery | STARTED, base 38805902c8a467c6a1af3c07587d5f7b6ac68be5. Pending documentation restored byte-for-byte after temporary preservation; older stash retained. |
| docker compose -p checkstock_fbs_delivery_20261115 config -q | Passed without displaying configuration values. |
| docker compose -p checkstock_fbs_delivery_20261115 up --build -d --wait | Both services healthy; fresh database migrated to 0002_fbs_delivery. |
| docker compose -p checkstock_fbs_delivery_20261115 exec -T app python -m app.seed, twice | All five application tables were empty before seeding. Full row checksums unchanged after replay. |
| docker compose -p checkstock_fbs_delivery_20261115 exec -T app pytest -q | 398 passed in 45.46s. Full public-table checksums unchanged after tests. |
| docker compose -p checkstock_fbs_delivery_20261115 exec -T app alembic check | No new upgrade operations detected. |
| docker compose -p checkstock_fbs_delivery_20261115 exec -T app python -m pip check | No broken requirements found. |
| npm.cmd ci --ignore-scripts; CHECKSTOCK_BROWSER_URL=http://127.0.0.1:18093 npm test (PowerShell environment equivalent) | 46 passed in 55.5s using Chromium. The dependency audit notice is recorded below. |
| Real page with real HTTP/PostgreSQL, no mocked API | Reserve and ship 4 SKU-101, rename/archive the source, confirm delivery. Physical stock stays 21 and reserve stays 0; FBS transit changes 4 -> 0. Three operation events, old snapshots preserved. |
| Real HTTP replay and browser reload | Repeated deliver and earlier ship return delivered without new rows or balance changes; the delivered row has no further actions after reload. No page errors. Desktop and narrow screenshots inspected. |
| Existing local application upgrade on port 8000 | Same tested image applied with docker compose up -d --no-build --no-deps --wait app. All five application-table row checksums unchanged across migration; health and alembic check pass. |
| Read-only check of the existing local panel | New delivery button visible; existing totals remain 20 physical, 0 reserved and 4 FBS transit, with 9 operations and 14 events. No delivery command sent against the user's demonstration. |

New PostgreSQL tests exercise safe delivery replays (including a previous ship and reserve-create), invalid states/kinds, independent concurrent HTTP/database sessions, injected rollback after status/event changes, source archival/rename and another active reservation. Migration tests cover populated upgrade, undelivered downgrade/upgrade and refusal to downgrade delivered history.

Temporary build logs, hash manifests, screenshots and JSON observations are outside the repository in the local checkstock-fbs-delivery-5_ylve4j temporary directory. They contain no environment file contents or credentials. The full review-integration unittest suite was not rerun: its controller and tests did not change.

## Existing dependency notice

The unchanged optional browser-test dependency @playwright/test 1.55.0 makes npm audit report two high-severity package entries for the same [GHSA-7mvr-c777-76hp advisory](https://github.com/advisories/GHSA-7mvr-c777-76hp). The report concerns the Playwright browser installer and lists 1.55.1 as patched. npm audit exits 1; it is not reported as passing. No dependency update or browser download was performed as part of this extension. Existing cached Chromium was used; these packages are not application runtime dependencies.

## Publication and handoff

At the remote check, published dev and main both point to 38805902c8a467c6a1af3c07587d5f7b6ac68be5. The pending first-run/PowerShell/port instructions, personal demonstration script and this FBS extension are not in main yet. No implementation commit, push or Claude review has been performed for this stage. The candidate awaits the user's explicit pre-commit approval; the existing review workflow then applies before the user-owned main integration.

Proposed commit message: feat: confirm FBS delivery and refresh usage instructions
