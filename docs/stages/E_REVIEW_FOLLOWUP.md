# Deferred Stage E P3 corrections

Stage F inclusion: after PR #5 was merged at `91f8c21e131a294e0db5afc3d0167c83bcfcb50f`, the user authorized both corrections as part of `stage-f-delivery`. The controller recorded the clean published base `62e129b14bbaa5e5f22307e8df7e74a70a7f9bdc`; the preserved patch and drafts were restored byte-for-byte afterward. The standalone evidence below is historical; fresh combined F verification belongs in `docs/VERIFICATION.md`.

## Scope and approval boundary

The Stage E review approved base `6c6652253e24c1b5437d8e3daeba12be87afbdbf` through head `62e129b14bbaa5e5f22307e8df7e74a70a7f9bdc`, with two nonblocking P3 findings. The user requested local corrections without a commit, with their inclusion and independent review deferred to Stage F. The Stage E PR retains the exact approved head and excludes this patch. E's approval does not cover these corrections.

Preserve this patch while establishing the required clean, published Stage F base. Include the implementation, tests and decision update in F's approved scope and fresh verification. Do not reset E's base or reopen its review. This preparation does not start the remaining Stage F work.

## Changes

- `app/static/app.js`: generate UUID v4 operation keys from `crypto.getRandomValues` when `crypto.randomUUID` is unavailable on an HTTP origin. Generate the submission inside the form's error boundary. If secure randomness fails, show a Russian inline error, send no request and restore editable controls. Existing unresolved payload/key persistence and safe replay remain intact.
- Use a shared warehouse-error formatter for creation, rename and archive. An uncertain response, including 5xx after a possible commit, explicitly asks the user to refresh and check the warehouse list before retrying. Definite 4xx errors retain their existing message; no mutation is automatically retried.
- `tests/browser/panel.spec.js`: add eight regressions for the UUID fallback, persistence across reload, distinct fresh-operation keys, randomness failure, uncertain warehouse responses and definite rejections. Manual refresh exposes a possible committed change without a second mutation.
- `docs/DECISIONS.md`: record the compatibility and retry decisions.

No database schema, API, dependency, transaction, inventory rule or catalog idempotency contract changes. The patch is limited to the two requested P3 findings.

## Local verification on 2026-11-15 (Asia/Omsk)

A temporary Git index exported the approved E head plus the three modified files above at tree `c68b93560841397ecabd95f9a4933352a6e486ed`. This evidence document was added afterward. The real Git index remained empty. Existing untracked `DEMO_SCRIPT.md` and `docs/VERIFICATION.md` were excluded.

Checks used isolated Compose project `checkstock_e_p3_20261115`, loopback port 18088 and a fresh PostgreSQL volume. Runtime settings were generated outside the export and never printed or packaged. Existing demo and earlier validation projects were preserved.

| Command/check | Actual result |
| --- | --- |
| `git diff --check`; `node --check app/static/app.js`; browser test syntax/discovery | Passed. |
| `docker compose config -q` | Passed. |
| `docker compose up --build -d --wait --wait-timeout 180` | Built successfully; application and database healthy. |
| `docker compose exec -T app pytest -q tests/test_web.py` | **5 passed in 0.91s**, using the real PostgreSQL fixture. |
| `docker compose exec -T app alembic check` | No new upgrade operations detected. |
| Optional Playwright suite against exported page/assets | **30 passed in 39.9s**, Chromium, zero retries: the previous 22 cases plus eight new regressions. |
| Read public application tables and disposable test schemas afterward | All five application tables empty; no test schemas remained. No demo seed was requested. |
| Compare image panel assets, exported browser sources and workspace | Normalized hashes matched; later-stage drafts absent. |

Playwright ran through the existing pinned temporary installation's `playwright/cli.js test --config <export>/tests/browser/playwright.config.js`, with its node_modules on `NODE_PATH` and `CHECKSTOCK_BROWSER_URL=http://127.0.0.1:18088`. Every API request is intercepted by fixtures; these browser cases verify rendered behavior, not database transaction semantics. The randomness fallback is tested by making `randomUUID` unavailable before page scripts run.

No application or browser test failed. The first local hash-audit reader incorrectly assumed UTF-16 for a UTF-8 JSON output; correcting its decoding allowed the audit to pass without source changes. The full application suite and review-controller tests were not rerun for this JavaScript-only behavior change. Fresh combined verification and Claude review remain deferred to F. The unchanged E PR retains its own 369-test PostgreSQL and 22-test browser evidence.
