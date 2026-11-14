# CheckStock repository instructions

## Project contract

Read `CHECKSTOCK_CODEX_PLAN.md` before implementation. Follow its stages A-F and the decisions in `docs/DECISIONS.md`; use `docs/ACCEPTANCE.md` for the expected results. Keep the prototype within the documented scope.

Use English for new instructions, review prompts, commit messages, and review integration documentation. Preserve the existing Russian product interface and project documents unless a task requests their translation.

Never read, print, commit, or include `.env`, private keys, access tokens, or credentials in a review package. Do not add unrelated files to a commit. The existing application implementation may be untracked: untracked files are excluded from stage review, and their presence does not authorize staging or publishing them.

## Required independent stage review

The active coding agent follows this workflow for each implementation stage. This is a synchronous part of the current task, not a scheduled or background automation.

1. Read the stage requirements and describe the intended scope and checks.
2. Before changing implementation files, run `python scripts/review/local_review.py start --stage <slug>`. Start requires local `dev` to match published `dev` and tracked files to be clean. It records the immutable `BASE_SHA`. Use a new slug for a new stage; never reset the base to hide earlier changes or findings.
3. Implement only the authorized scope. Run checks appropriate to the change and record their actual results and limitations.
4. Commit the authorized stage files on `dev` with an English commit message. Publish the committed stage to `dev` through `git@github-personal:vladicem0/CheckStock.git`, then run `python scripts/review/local_review.py review --stage <slug>`. Do not publish `main` unless the user requests it.
5. Read the complete structured result and supporting logs. An exit code alone is insufficient.

Handle review results as follows:

- `APPROVED`: accept only a valid result for the recorded base and the exact reviewed commit. Proceed when the required checks also pass. Approval does not cover later commits; `status` rejects an approval if local or published HEAD has changed.
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
