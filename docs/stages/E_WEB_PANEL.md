# Stage E: local warehouse dashboard

## Scope and review boundary

Stage: `stage-e-web-panel`. Immutable base: `6c6652253e24c1b5437d8e3daeba12be87afbdbf`.

The user accepted D and merged PR #4 at `fc75931f31e0627a4d03ba52b84eca4307bacb89`. Published dev remained at D's approved head; published main and dev had identical trees. The controller successfully recorded E's base on clean tracked dev before E implementation. The six tracked deferred-P3 files were temporarily stashed and restored byte-for-byte from a backup; the other six E source/draft files were also preserved. Only that temporary stash was removed. The unrelated reviewer-transport stash remains intact.

The candidate includes both P3 corrections deferred from D, the Russian dashboard, its tests and relevant documentation. It excludes the untracked final-delivery files `DEMO_SCRIPT.md` and `docs/VERIFICATION.md`. No commit, push or independent Claude review is claimed for this candidate. Those actions require the user's approval of the concrete E scope and verification below. Main integration remains user-owned.

## Implementation and decisions

- Render `/` with Jinja2 and serve local CSS/JavaScript. The existing API remains the only interface for stock commands and reports; no new domain endpoint, schema, migration, SPA build or frontend service is introduced.
- Show the full SKU/warehouse matrix, zero and archived cells, physical stock, FBS reserves and available stock. Display FF, FBO and FBS transit separately, with routes, statuses and operation IDs.
- Provide receipt, transfer, FBS and FBO forms and state-dependent dispatch/receive/cancel buttons. Show inline Russian errors, including local available/requested quantities, and reload reports after mutations.
- Keep archived stock visible, permit outbound evacuation, and let older operations finish. A selected warehouse that becomes unavailable is not silently replaced by another warehouse.
- Support warehouse creation, inline rename, archive confirmation and immutable history. Preserve unsaved rename input during refresh. Display device-local time with exact UTC and full-ID tooltips.
- Preserve a complete unresolved stock-command payload, selection labels and idempotency key in sessionStorage. Freeze that intent and offer a safe replay after a lost/malformed response, 5xx or reload, including when its warehouse is subsequently archived. Definite 4xx rejections allow editing. Clear quantity after success so another create is an explicit new action. If browser storage is unavailable, retry state lasts only within the open page; README documents this limit.
- Ignore obsolete refresh successes and failures; distinguish committed mutations from failed report refreshes. Keep pending action guards outside replaceable DOM controls. Insert names and messages as text.
- Include deferred P3 report projections with bounded query counts and fresh ORM objects. Include safe database-error logs and 503/500 classification, preserving transaction rollback and sanitized replies.
- Add pinned Jinja2 3.1.6; existing MarkupSafe 3.0.4 already satisfies it. All 30 Python runtime/test package versions remain constrained. Add a separate optional Playwright 1.55.0 test package and lockfile; Node.js is required only for these tests. Browser dependencies/results and review tests are excluded from the application image.

The stock matrix and its three transit lists share the existing single PostgreSQL snapshot. The three separate report HTTP requests are not represented as one cross-request database snapshot. Full authentication, external marketplaces, partial operations, delivery confirmation, filters and final demonstration material remain outside E.

## Included files

| Area | Files |
| --- | --- |
| Web entry and assets | `app/main.py`, `app/templates/base.html`, `app/templates/index.html`, `app/static/app.js`, `app/static/app.css` |
| Deferred P3 | `app/services/inventory.py`, `tests/test_report_safety.py`, `tests/test_http_errors.py`, `tests/test_health.py` (error handler changes are in main) |
| Web HTTP checks | `tests/test_web.py` |
| Optional browser checks | `tests/browser/package.json`, `package-lock.json`, `playwright.config.js`, `support.js`, `panel.spec.js`, `README.md` |
| Dependency/build exclusions | `pyproject.toml`, `constraints.txt`, `.dockerignore`, `.gitignore` |
| Documentation | `README.md`, `docs/DECISIONS.md`, `docs/stages/D_REVIEW_FOLLOWUP.md`, this document |

This is a 24-file allowlist. The final review must include all of it, including the P3 patch and optional browser test sources. No unrelated draft or existing stash is authorized for publication.

## Automated verification on 2026-11-15 (Asia/Omsk)

The 23-file executable/test candidate was exported through a temporary Git index at tree `8596d0d45a99ba084f63db72ef954cca349c050a`. This evidence document is the 24th file, added after the runs. Implementation, tests, dependencies and build settings remain identical to that verified export. The real Git index was not staged.

Runtime checks used isolated Compose project `checkstock_stage_e_20261115`, loopback port 18087, and a new named PostgreSQL volume. Runtime settings were generated outside the export and never printed or packaged. Existing demo and earlier validation projects were preserved.

| Command/check | Actual result |
| --- | --- |
| Fetch branches, inspect PR #4 and compare published trees | PR #4 merged; published main/dev trees identical; local dev matched published dev. |
| `python scripts/review/local_review.py start --stage stage-e-web-panel` | STARTED at the immutable base above, on clean tracked dev. |
| `git diff --cached --check` on the temporary candidate index | Passed after removing trailing blank lines from two newly included documents/test files. |
| `docker compose config -q` | Passed. |
| `docker compose up --build -d --wait --wait-timeout 180` | Image built; app and PostgreSQL healthy. The dependency layer installed the exact 30 constrained packages. |
| Inspect PostgreSQL | 17.12 (Debian 17.12-1.pgdg13+1). |
| Read application tables before seed | All five empty. |
| `docker compose exec -T app python -m app.seed`, twice | 25 physical units, no reserves/transits, six cells and four receipts/events; all row values including IDs/timestamps identical after the repeat. |
| `docker compose exec -T app pytest -q` | **369 passed in 44.28s**, real PostgreSQL: 353 D cases, 11 deferred-P3 regressions, five web HTTP checks. |
| `docker compose exec -T app alembic check` | No new upgrade operations detected. |
| Dependency check within pytest | All 30 installed package pins matched and `pip check` passed. |
| Optional Playwright suite against the exported page/assets | **22 passed in 26.5s**, Chromium revision 1187; zero retries. |
| Compare demo rows after pytest and mocked browser checks | All unchanged; no disposable test schemas remained. |
| Compare running-image implementation, tests, config and vendor hashes with export | Matched; source/config line endings normalized and vendor bytes compared exactly. F files absent. |
| Compare six browser test files with the export | Hashes matched the sources used for the passing run. |

The Playwright dependencies were installed separately with npm lifecycle scripts disabled, using the pinned package and lockfile. The executed command used that temporary installation's `playwright/cli.js test --config <export>/tests/browser/playwright.config.js`, `NODE_PATH` pointing to its node_modules, and `CHECKSTOCK_BROWSER_URL=http://127.0.0.1:18087`. The documented reproducible path is `npm ci --ignore-scripts` and `npm test` in the exported/repository browser-test directory.

Browser fixtures use the actual HTML/CSS/JavaScript but intercept every API request, including unexpected mutations; only shell/assets reach the running app. They verify matrix zeros/archives, three transits, all command forms and status-action routes, local shortage quantities, preserved payload/key after lost response/503/malformed success and reload, post-commit refresh failure, obsolete failed refreshes, pending actions across rerenders, rename drafts, archive selectors, hostile names as text, and narrow-screen containment. They are not PostgreSQL integration evidence; that comes from pytest and the real workflow below.

## Visible workflow against real PostgreSQL

All commands below were entered through the visible panel on the isolated project. No mocked API or direct stock writes were used. These steps form one continuous scenario; the independent initial-state T01–T13 scenarios remain covered by the PostgreSQL suite.

| Visible action | Observed result |
| --- | --- |
| Initial page | Six cells, including zeros; physical 25, reserve 0, all transits 0. |
| Reserve four SKU-101 on North, then ship | North 10/4/6 before shipment, then 6/0/6; total physical 21 and FBS transit 4. |
| Reserve three more SKU-101, then cancel | Physical unchanged; reserve returned to zero; cancellation history visible and terminal actions removed. |
| Transfer three SKU-101 North to South | Draft preserved balances and added its history event; dispatch showed North 3, South 6, FF transit 3; receipt showed North 3, South 9, FF transit 0. |
| FBO three SKU-102 from North, then receive | North fell from 5 to 2; FBO transit 3 then 0; receipt did not credit stock back. |
| Reserve one SKU-103 on empty North | Inline shortage showed available 0/requested 1, unchanged stock/history. |
| Transfer two SKU-103 South to North, then reserve one | North stayed 0 in transit, became 2 on receipt; reservation then succeeded and was cancelled successfully. |
| Create East and receive two SKU-103 there | Nine matrix cells including zero cells; receipt raised total physical to 20. |
| Create incoming East-to-South draft; rename North | Rename changed current displays while earlier events retained the original North name. |
| Archive South with 11 units | All stock/history remained; destination selection became an explicit active-warehouse placeholder, while South remained an outgoing archive option. |
| Complete the pre-archive incoming draft | Dispatch and receipt succeeded; South received its one unit despite now being archived. |
| Evacuate two SKU-101 from archived South to East | Draft, dispatch and receipt succeeded with the expected transit and destination changes. |
| Reserve remaining three SKU-101 on renamed North; attempt FBO one | Physical 3/reserve 3/available 0; FBO rejected with 0 available/1 requested and no additional event. |
| Send one SKU-102 FBO and one SKU-103 FF transfer for the final view | All three transit cards populated simultaneously. Final totals: physical 18, reserve 3, available 15, FF 1, FBO 1, FBS 4. |

A separate read-only PostgreSQL verification matched the final view: three products, three warehouses, nine cells, 16 operations, 29 events, nonnegative balances and reserves no greater than physical stock. The archived warehouse remained South; no disposable test schemas remained. A desktop screenshot and 29 compact UI observations were saved outside the repository. Browser warning/error logs were empty.

The isolated demo intentionally retains these verification operations for inspection; it is not the untouched 25-unit seed fixture. Working/demo project data elsewhere was not cleared or modified.

## Problems, limitations and handoff

The obsolete draft needed corrections for unresolved-request recovery, stale refresh failures, hidden shortage quantities, pending controls and discarded rename input; those were corrected before candidate validation. Initial whitespace validation identified trailing blank lines in two newly included files, corrected before the build. All application and browser tests passed on their first execution; no test failure was hidden or substituted with earlier evidence.

Review-controller tests were not rerun because its code did not change. No new Claude approval is claimed. The current task stops for user approval before the proposed commit:

`feat: add stage E warehouse dashboard and deferred review fixes`

After approval, commit only the allowlist on dev, publish it, and run the complete E independent review against the recorded base. Stage F and main integration are not authorized by preparation of this candidate.
