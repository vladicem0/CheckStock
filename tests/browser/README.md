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

On Windows PowerShell, `npm` and `npx` may resolve to `npm.ps1` and `npx.ps1`.
If they fail with `PSSecurityException` because script execution is disabled,
allow scripts for the current session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Run this in the same PowerShell window as the test commands and confirm if asked.
The `Process` scope affects that session and its child processes; it does not
change persistent user or machine settings. A new session uses the usual policy.
Organizational Group Policy can override this setting. See
[Microsoft's session-scope example](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/set-executionpolicy#example-6-set-the-execution-policy-for-the-current-powershell-session).

Alternatively, keep the policy unchanged and use `npm.cmd` instead of `npm` and
`npx.cmd` instead of `npx` in every Windows command below, including `npm.cmd test`.
These invoke the Node.js CMD launchers rather than PowerShell scripts.
Linux/macOS do not need this PowerShell setting.

Start an application built from the candidate being checked. From this directory:

```text
npm ci --ignore-scripts
npx playwright install chromium
npm test
```

The default URL is `http://127.0.0.1:8000`; `npm test` needs no URL override for
the standard setup. To set the address explicitly, use one of the following
commands. If `APP_PORT` differs, replace 8000 with that port:

```powershell
$env:CHECKSTOCK_BROWSER_URL = 'http://127.0.0.1:8000'
npm test
Remove-Item Env:CHECKSTOCK_BROWSER_URL
```

```sh
CHECKSTOCK_BROWSER_URL=http://127.0.0.1:8000 npm test
```

Use Node.js 18 or newer. `@playwright/test` and its dependencies are pinned in
`package-lock.json`; Chromium installation uses that Playwright version's pinned
browser revision. These are optional test tools, not an application dependency
or a frontend build. No package scripts run during `npm ci --ignore-scripts`.
No `.env` or credentials are needed. The URL must use local HTTP.

There are no automatic retries. Failure screenshots and traces are written to
ignored `test-results/`; they contain only this suite's synthetic fixture data.
Tests live outside the runtime image and run separately from `pytest`.
