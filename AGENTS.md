# CheckStock repository instructions

## Project contract

Read `CHECKSTOCK_CODEX_PLAN.md` before implementation. Follow its stages A-F and the decisions in `docs/DECISIONS.md`; use `docs/ACCEPTANCE.md` for the expected results. Keep the prototype within the documented scope.

Use English for new instructions, review prompts, commit messages, and review integration documentation. Preserve the existing Russian product interface and project documents unless a task requests their translation.

Never read, print, commit, or include `.env`, private keys, access tokens, or credentials in a review package. Do not add unrelated files to a commit. The existing application implementation may be untracked: untracked files are excluded from stage review, and their presence does not authorize staging or publishing them.

## Required independent stage review

The active coding agent follows this workflow for each implementation stage. This is a synchronous part of the current task, not a scheduled or background automation.

1. Read the stage requirements and describe the intended scope and checks.
2. Before changing implementation files, run `python scripts/review/local_review.py start --stage <slug>`. Start requires local `dev` to match published `dev` and tracked files to be clean. It records the immutable `BASE_SHA`. Use a new slug for a new stage; never reset the base to hide earlier changes or findings.
3. Prepare only the authorized stage. Run checks against the exact candidate tree and record actual results and limitations. Existing untracked files from later stages must not enter a build, test run, commit, or review package for the current stage.
4. Before committing a stage, present a concrete summary to the user: included files, completed work, decisions, functional changes, architecture changes, deviations or additions relative to the plan, checks and results, remaining work, and the proposed English commit message. Wait for the user's explicit approval of that stage. Preparing and testing a candidate is allowed; committing, pushing, and dispatching its review require that approval.
5. After approval, commit only the authorized stage files on `dev` with an English commit message. Publish the committed stage to `dev` through `git@github-personal:vladicem0/CheckStock.git`, then run `python scripts/review/local_review.py review --stage <slug>`. The existing controller sends the English review request through SSH and uses the separately maintained reviewer policy.
6. Read the complete structured result and supporting logs. An exit code alone is insufficient. Scoped corrections requested by the reviewer are covered by the approved stage: explain the fixes, rerun affected checks, commit and push the corrections to `dev`, and review the complete stage again with the original base. Obtain renewed user approval if a correction expands the approved scope, changes an agreed architecture, or introduces a plan deviation.
7. After a valid `APPROVED` result for the exact published head and passing required checks, report the reviewed SHA, result, fixes, and verification evidence to the user. Stop at this handoff. The user performs their own check and owns committing/integrating and pushing the result to `main`; do not do that on their behalf.
8. Begin the next stage only after the user confirms their `main` integration and authorizes continuation. Check the published branch state before starting. If review corrections affect a later stage, revise that stage's scope, implementation assumptions, and checks before its approval and review. Do not continue from an obsolete later-stage draft.

Handle review results as follows:

- `APPROVED`: accept only a valid result for the recorded base and the exact reviewed commit. Report the result when the required checks also pass, then wait for the user-owned main handoff. Approval does not cover later commits; `status` rejects an approval if local or published HEAD has changed. If the user explicitly requests further corrections within the same stage after approval, use `review --reopen-approved` for the changed published head. This retains the original base and consumes one of the same three correction rounds; it does not authorize unrelated work or a new stage.
- `CHANGES_REQUESTED`: inspect every finding, make the necessary scoped fixes, rerun affected checks, commit and publish the fixes, then review again with the original base. Each review covers the entire stage, not just the latest fix.
- `BLOCKED`: stop the review cycle and report the concrete blocker. Do not treat missing credentials, failed commands, timeouts, invalid output, missing evidence, or an unavailable reviewer as approval.

The limit is one initial review plus three fix reviews, iterations 0-3. If changes are still requested after the third fix review, stop and report the outstanding findings. Do not create a new stage or alter state files to evade this limit. Do not claim completion or approval while a required review remains blocked.

PowerShell equivalents:

```powershell
./scripts/Review-Stage.ps1 -Action Start -Stage <slug>
./scripts/Review-Stage.ps1 -Action Review -Stage <slug>
./scripts/Review-Stage.ps1 -Action Status -Stage <slug>
```

See `docs/INDEPENDENT_REVIEW.md` for setup, transport, result validation, state locations, and recovery. Keep infrastructure details out of the runtime review prompt; it identifies the stage, base, head, and review inputs without describing the developer or execution topology.

## Verification standards

Test the behavior changed, using the project's existing checks. Record commands, results, and checks that could not run. Never copy an earlier success report as evidence for the current commit.

For review integration changes, run:

```text
python -m unittest discover -s tests/review -v
```

For application behavior, use real PostgreSQL through the documented Compose setup:

```text
docker compose exec -T app pytest -q
docker compose exec -T app alembic check
```

Use focused application tests where appropriate, and a full application run when the scope requires it. Review integration tests do not establish that application tests pass. Keep destructive test cleanup confined to the disposable schemas created by the test fixtures; never clear the demo or working database.

Check database invariants, atomic balance/status/event changes, idempotency, local warehouse availability, and concurrent behavior when changing inventory logic. For interface changes, verify the relevant visible workflow as well as automated checks.

## Review policy maintenance

The remote reviewer policy is external to this repository at `~/.claude/skills/stage-review/SKILL.md`. Git checkout, normal review, and routine installer runs must not overwrite it. A policy change is a separate, intentional maintenance task: inspect the current policy, explain the exact change, preserve a recovery copy, and validate the updated policy. Do not weaken it merely to make a stage pass.
