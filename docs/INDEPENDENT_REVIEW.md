# Independent stage review

This integration adds an independent Claude review to each implementation stage. The active coding agent performs the workflow in the current task according to `AGENTS.md`. It does not install a scheduled job, background watcher, or recurring automation.

The review covers committed changes on `dev` from a fixed stage base to the current published head. Approval is tied to that exact pair of commits. The review is a separate check from application tests.

## Components and boundaries

| Component | Purpose |
| --- | --- |
| `scripts/Review-Stage.ps1` | PowerShell entry point for start, review, and status |
| `scripts/review/local_review.py` | Local stage state, publication checks, remote invocation, and result validation |
| `%LOCALAPPDATA%/CheckStock/reviews/<stage>/` | Local state and per-run logs |
| `dev@vpn` | SSH destination for the reviewer |
| `~/bin/review-stage.sh BASE HEAD STAGE ITERATION` | Remote review entry point |
| `~/.local/lib/checkstock-review/` | Remote runtime |
| `~/.local/share/checkstock-review/repository` | Dedicated review clone |
| `~/.local/state/checkstock-review/runs/` | Remote run artifacts |
| `~/.claude/skills/stage-review/SKILL.md` | External reviewer policy, maintained separately |

Local publication uses `git@github-personal:vladicem0/CheckStock.git`. The remote clone uses its own repository access. Credentials and private keys are not included in review inputs or logs. Application work and data are not stored in the dedicated review clone.

Repository `CLAUDE.md` supplies project context, invariants, and review standards. Runtime prompts are concise English instructions identifying the stage, base, head, and supplied inputs. They omit author identity and execution topology. For example:

```text
Review stage inventory-services, base <BASE_SHA>, head <HEAD_SHA>.
Assess the supplied diff and project context against the stage requirements.
Return the required structured review result.
```

Claude receives a read-only snapshot of the diff and relevant context. Bash, Edit, and Write tools are not available during review. Source files, diffs, and logs are untrusted review material, not instructions that can replace the reviewer policy.

## User approval and main integration gates

The user owns two explicit handoffs for each stage:

1. Before the first stage commit, the coding agent presents the exact file scope, completed work, decisions, functional and architectural changes, plan additions/deviations, actual verification, remaining work, and an English commit message. The agent waits for approval before committing, pushing `dev`, or dispatching Claude review.
2. After Claude approves the exact published head and required checks pass, the agent reports the SHA and evidence and stops. The user checks the result and commits/integrates and pushes it to `main`. The agent does not publish `main` or begin the next stage until the user confirms that handoff and authorizes continuation.

Reviewer-requested fixes within the approved scope may be implemented, tested, committed to `dev`, pushed, and re-reviewed under the original stage approval and immutable base. The agent reports the corrections. An expanded scope, changed agreed architecture, or new deviation needs renewed user approval. The existing one-initial-plus-three-fix-review limit still applies; an exhausted or blocked cycle is reported, never treated as approval.

If a fix changes assumptions for a subsequent stage, update its proposed scope and checks before that stage reaches approval or review. Prompts, commit messages, and review integration documents are English; user-facing summaries may remain Russian.

## Importing the existing application in stages

The initial application already exists as untracked local files. Those drafts predate this review workflow and have not received an independent approval. Import them as new, explicit stage changes from a recorded published `dev` base; do not reconstruct or claim a historical pre-implementation review. Because these files are absent from the recorded base, the eventual committed additions remain fully visible in the stage diff.

Preserve the complete local drafts while preparing each stage. Review the actual candidate content, not merely a list of filenames. In particular, the full current `app/main.py` imports later API/UI components, so it cannot be included unchanged in infrastructure stage B. Stage B needs a working health-only entry point, stage D adds API/Swagger, and stage E adds the web panel. Early documentation and package-data declarations must likewise match the candidate stage.

Build and test a clean export of the exact candidate Git index (or an equivalent isolated checkout), containing only the tracked stage content. Do not copy the entire working directory: `Dockerfile` uses `COPY . .`, and later-stage untracked Python files/tests could otherwise make an incomplete commit appear runnable. Use an isolated Compose project and disposable test schemas. Preserve existing demo data and the existing review integration tests.

The intended application commits follow the contract:

| Stage | Commit content and validation boundary |
| --- | --- |
| A | Domain decisions and acceptance criteria; align the human approval/handoff documentation. No application behavior changes. |
| B | Configuration, database primitives/models, migration, receipt service, explicit seed, minimal health endpoint, Compose, dependency metadata, safety ignores, and seed/schema checks. |
| C | Remaining inventory commands and warehouse lifecycle, read models, lifecycle/concurrency/rollback/invariant tests; no HTTP feature routes or web panel. |
| D | Pydantic contracts, HTTP routes, error handling, OpenAPI and local Swagger assets, HTTP/snapshot tests. |
| E | Jinja templates, local panel JavaScript/CSS, root page route, and browser/web checks. |
| F | Complete README, demonstration script, final verification report, and clean-start acceptance of the reviewed implementation. |

At stage B, separate application pytest discovery from the existing `tests/review` suite: those integration tests invoke the Git executable, which is not present in the application image. Run that suite separately with its documented host unittest command; do not silently treat it as application coverage.

Use focused tests and current evidence per candidate. The earlier full application run is historical context, not proof that an intermediate stage builds or passes. Reconcile these boundaries after each approved correction rather than committing later-stage drafts mechanically.

## Start before implementation

Choose a unique stage slug and ensure that local `dev` matches published `dev`, with no tracked worktree changes. Read the plan and define the stage scope first.

```powershell
./scripts/Review-Stage.ps1 -Action Start -Stage inventory-services
```

Equivalent Python command:

```text
python scripts/review/local_review.py start --stage inventory-services
```

Start records `BASE_SHA` and does not overwrite an existing stage's base. A new implementation stage needs a new slug. Starting after implementation would omit earlier work from the comparison and is not a valid substitute for starting beforehand.

Untracked files are deliberately excluded from review. At initial integration, the application files are present locally but only the plan was previously committed. Do not infer that a review of integration commits validates those application files. Adding or publishing them requires that work to be within the user's requested scope. Avoid broad `git add .` when unrelated files are present.

## Implement, verify, commit, and review

Implement the authorized stage, run the appropriate checks on its exact candidate tree, and present the stage summary for user approval. After that approval, commit only the intended files on `dev` using an English message. Publish the commit to `dev` through the personal SSH alias; do not publish to `main` unless requested.

```text
git push git@github-personal:vladicem0/CheckStock.git dev:dev
```

Run the review:

```powershell
./scripts/Review-Stage.ps1 -Action Review -Stage inventory-services
./scripts/Review-Stage.ps1 -Action Status -Stage inventory-services
```

Equivalent Python commands:

```text
python scripts/review/local_review.py review --stage inventory-services
python scripts/review/local_review.py status --stage inventory-services
```

The wrapper also supports `-Repo`, `-StateDir`, `-SshHost`, `-SshUser`, `-RemoteScript`, `-GitUrl`, and `-TimeoutSeconds`. Defaults target the locations above; the remote script default is `/home/dev/bin/review-stage.sh` and the timeout is 960 seconds. Configuration changes do not authorize a different repository or publication scope.

## Exact revision checks

For every invocation, the remote runner fetches `origin dev` and checks the dedicated clone:

- The worktree is clean and the checked-out branch is `dev`.
- Updating to the remote branch is fast-forward only.
- A local branch ahead of or diverged from the remote is rejected.
- The requested head equals the fetched published head.
- The base and head define the stage being reviewed.
- The expected branch and exact head remain valid after the review.

A changed published head, dirty checkout, invalid ancestry, failed fetch, or other failed prerequisite blocks the result. The runner does not reset or clean away unexpected work to make a review proceed.

The local validator accepts only a valid structured result for the requested stage, base, head, and iteration. Invalid JSON, schema violations, missing required evidence, failed commands, timeouts, or an unavailable reviewer produce `BLOCKED`, never an inferred approval. Read the result and logs, rather than relying only on a process exit code.

## Results and bounded fixes

| Status | Exit code | Required action |
| --- | ---: | --- |
| `APPROVED` | 0 | Accept only for the reviewed revision and when required checks pass |
| `CHANGES_REQUESTED` | 2 | Inspect findings, apply scoped fixes, verify, commit, publish, and review again |
| `BLOCKED` | 3 | Stop and report the concrete blocker and available evidence |

There is one initial review, iteration 0, and at most three fix reviews, iterations 1-3. Every fix review uses the original `BASE_SHA`, so it assesses the full stage and whether earlier findings are resolved. Fix attempts follow `CHANGES_REQUESTED` and a changed committed head.

If iteration 3 still requests changes, stop and report the remaining findings. Never alter the base, delete state, or use a new slug to bypass the review limit. If a prerequisite fails, resolve the underlying issue deliberately; do not report the stage as approved or complete. A later commit invalidates approval, and `status` rejects a stale approval when the local or published head moves.

## Evidence and verification

Local state is under `%LOCALAPPDATA%/CheckStock/reviews/<stage>/state.json`; run artifacts are under its `runs/<run-id>/` directory. Remote artifacts are under `~/.local/state/checkstock-review/runs/`. Use these records to diagnose blocked runs and correlate a result with its exact commit range. Do not manually edit state to manufacture approval.

For review integration changes:

```text
python -m unittest discover -s tests/review -v
```

For application changes, follow the PostgreSQL setup in `README.md` and run checks appropriate to the affected behavior:

```text
docker compose exec -T app pytest -q
docker compose exec -T app alembic check
```

An integration test run or a successful independent review does not prove that application tests passed. Report commands actually run and their results. Existing `docs/VERIFICATION.md` entries describe earlier checks, not automatic evidence for the current stage.

The application fixtures create and remove only their own randomly named PostgreSQL test schemas. Never clear the working schema or demo data to prepare a review.

## External policy maintenance

The external policy at `~/.claude/skills/stage-review/SKILL.md` is not part of a repository checkout. Normal review runs, Git updates, and routine installer executions must preserve an existing policy instead of replacing it with repository content.

Update this policy only as an intentional maintenance task:

1. Inspect the existing policy and describe the exact intended change.
2. Preserve a recovery copy of the current policy.
3. Apply the reviewed change explicitly, outside ordinary checkout and review execution.
4. Validate the policy and perform an appropriate smoke review, recording the result.

Do not weaken or replace the policy merely to clear a finding. Repository context may evolve with the application, but changes to `CLAUDE.md` do not authorize an automatic external policy update.

## Installation and recovery

The installed runner is a trusted copy, not an import from the review clone. Its configuration is `~/.config/checkstock-review/config.json`; it names the dedicated clone, repository URL, state directory, policy, Claude executable, and a 900-second review timeout. This account already has GitHub access through `github-checkstock` and Claude subscription authentication. No new tokens are required.

To deliberately update the runtime, first inspect and test the runtime files, then copy them to a staging directory and invoke the installer:

```powershell
ssh -l dev vpn 'mkdir -p /home/dev/.cache/checkstock-review-install'
scp scripts/review/remote_review.py scripts/review/protocol.py scripts/review/review.schema.json scripts/review/review-stage.sh scripts/review/install-remote.sh dev@vpn:/home/dev/.cache/checkstock-review-install/
ssh -l dev vpn 'bash /home/dev/.cache/checkstock-review-install/install-remote.sh'
```

`install-remote.sh` preserves an existing policy and configuration. A first installation requires a separately reviewed policy file as its first argument; that policy is maintained outside Git. It never obtains new policy from the branch under review. Ordinary start/review/status operations do not reinstall anything. Back up installed runtime files before an intentional upgrade if a rollback may be needed.

The skill body is loaded explicitly as the system instruction and its SHA-256 digest is logged. Automatic skills, project settings, hooks, plugins and MCP discovery are disabled. Claude runs with `--safe-mode`, `--restricted`, and only Read/Grep/Glob tools confined to the snapshot. The snapshots contain exact tracked blobs from both commits, plus a diff and request metadata. Source execution, including application tests, remains the developer's responsibility. This is tool-level isolation under the existing account, not a separate operating-system security boundary.

The result schema uses a deliberately small, validated subset of JSON Schema, with no Python package dependency. Unknown schema keywords fail closed. The CLI envelope must contain successful `structured_output`; prose is never parsed for a verdict. Both sides check commit identity and status consistency. SSH errors can have exit codes other than 3; the local controller converts them into `BLOCKED`.

For a blocked run, inspect the local run's `result.json`, `metadata.json`, `stdout.txt` and `stderr.txt`. The remote run adds `claude.stdout.json`, `claude.stderr.log`, the snapshot and synchronization evidence. Authentication failures require repairing the existing login, then retrying the same stage. A timeout terminates the Claude process group; the file lock is released by the operating system. A concurrent review reports `BLOCKED` rather than sharing a mutable clone.

The installed Claude version uses JSON Schema draft-07; do not change the schema dialect without checking CLI compatibility. An account may report a successful login while its API calls are blocked pending acceptance of updated terms. In that case, the account owner must review the terms in Claude's website. The runner reports the API diagnostic as `BLOCKED`; changing permissions, bypassing safeguards, or retrying repeatedly does not resolve it.

If the dedicated clone is dirty, ahead, divergent or on another branch, preserve and investigate that state manually. The runner never resets it. To recreate it, intentionally configure a new empty dedicated directory; keep the old directory for recovery. If GitHub `dev` advances during review, reconcile your local branch, retain the original stage base, and review the current published head. Do not reuse the old verdict. Results and snapshots stay outside Git; archive them separately when no longer needed.
