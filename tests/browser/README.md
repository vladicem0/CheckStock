# Optional panel browser checks

These tests execute the actual rendered panel, CSS and JavaScript in Chromium.
Every `/api/` request is intercepted before navigation and answered by an explicit
test fixture. Unexpected requests fail the test and never reach the application
API. Only local GET requests for the shell and static assets reach the server.
The suite does not seed, mutate or clear a database.

This isolates browser behavior: forms and status actions, archived selections,
zero cells and transit rendering, safe text insertion, lost-response retries,
overlapping refreshes, pending actions, rename drafts and narrow layouts. It does
**not** prove PostgreSQL invariants or API correctness. Run the project's real
PostgreSQL pytest suite and the visible end-to-end workflow as well.

Start an application built from the candidate being checked. From this directory:

```text
npm ci --ignore-scripts
npx playwright install chromium
npm test
```

The default URL is `http://127.0.0.1:8000`. For a separate Compose project, set the
test URL before `npm test`:

```powershell
$env:CHECKSTOCK_BROWSER_URL = 'http://127.0.0.1:18087'
npm test
```

```sh
CHECKSTOCK_BROWSER_URL=http://127.0.0.1:18087 npm test
```

Use Node.js 18 or newer. `@playwright/test` and its dependencies are pinned in
`package-lock.json`; Chromium installation uses that Playwright version's pinned
browser revision. These are optional test tools, not an application dependency
or a frontend build. No package scripts run during `npm ci --ignore-scripts`.
No `.env` or credentials are needed. The URL must use local HTTP.

There are no automatic retries. Failure screenshots and traces are written to
ignored `test-results/`; they contain only this suite's synthetic fixture data.
Tests live outside the runtime image and run separately from `pytest`.
