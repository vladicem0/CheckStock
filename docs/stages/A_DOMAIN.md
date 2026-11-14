# Stage A candidate: domain contract and approval workflow

Date: 2026-11-14 (Asia/Omsk).
Review stage: `stage-a-domain-contract`.
Immutable base: `622bb1e3b0ad82a9334b8c8f883ff67d35acca44`.

## Candidate scope

- `docs/DECISIONS.md`: domain states, exact balance/reserve/transit effects, invariants, archival rules, transaction ownership, lock ordering, command identity, history, and seed assumptions.
- `docs/ACCEPTANCE.md`: the R01-R15 requirement matrix and T01-T14 expected results, including failure and concurrent cases. These are acceptance criteria, not assertions that the current stage implements the application.
- `AGENTS.md` and `docs/INDEPENDENT_REVIEW.md`: the user-requested approval before commit/push/review, user-owned main integration after exact-head approval, bounded correction cycle, and safe import boundaries for the pre-existing local drafts.
- This file: candidate scope, decisions, verification evidence, and limits.

There are no application code, schema, deployment, or product behavior changes in this stage. The application drafts already present locally remain outside the candidate and are not covered by this stage's independent review.

## Decisions and plan relationship

The target remains the contract's synchronous FastAPI/SQLAlchemy/PostgreSQL monolith with Alembic and a local Jinja interface. Stock is checked at the selected warehouse, transit is derived from operation states, removal means archiving, and all stock/status/event effects are transactional. The acceptance matrix defines the exact time at which each quantity changes.

Concrete technical choices documented for later stages include UUID identifiers, database-enforced append-only events, transaction-scoped creation-key serialization, ordered row locks, and consistent report snapshots. These strengthen the stated accounting requirements without adding business features. Their implementation and runtime evidence belong to the later stages.

The human approval and main-integration gates are an explicit addition requested by the user on 2026-11-14, outside the original application's A-F feature scope. They change the delivery process, not the product architecture or functionality. The initial candidate did not change the reviewer controller, external Claude policy, result schema, or correction limit. The follow-up below adds explicit controller support for the user-requested repeat review; the external policy, result schema, and correction limit remain unchanged.

## Verification performed for the initial candidate

- `python scripts/review/local_review.py start --stage stage-a-domain-contract`: STARTED; local and published dev matched the immutable base above. No reviewer was invoked by this action.
- Document coverage assertions: all R01-R15 and T01-T14 entries are present; state names, stock/reserve invariants, idempotency, and row locking are present in the decisions document.
- Read-only reconciliation of the domain documents against the implementation contract and review of the staged-import boundaries.
- `python -m unittest discover -s tests/review -v`: 87 tests passed in 49.798 seconds from an isolated export of the exact candidate Git index, excluding all later-stage untracked application files and unrelated local runner changes. This validates the existing review integration, not the warehouse application.
- `git diff --check`: passed for the tracked process-documentation changes.
- Read-only SSH preflight to `dev@vpn`: the installed review entry point is executable and the external reviewer policy is readable. No policy contents, credentials, or application files were transmitted as a review request.

No application code changed, so the historical PostgreSQL run was not repeated or claimed as current stage evidence. These checks preceded publication of the initial candidate as `89fadb3776f5c619b2dc5d34f6b0e7d88941e3b9`. Its independent review returned `APPROVED` at iteration 0 with three optional P3 documentation findings; that approval does not cover the follow-up changes.

## Later-stage preparation issues

Stage B must validate an isolated candidate tree with a health-only entry point. The existing complete local main module imports later API/UI components and cannot be committed unchanged at that boundary. Future untracked code/tests must not leak into the build.

Application pytest discovery must also be separated from the existing `tests/review` suite when preparing stage B: that suite invokes Git, which is not installed in the application image. Continue running the review suite separately through its documented host unittest command. This preparation item is recorded here and has not been implemented ahead of the user's stage handoff.

## Follow-up: three P3 corrections and explicit repeat review

On 2026-11-14, the user requested all three P3 corrections within stage A, publication to dev, and another independent review before the main handoff. The corrections are:

- Display the actual FBO statuses `in_transit` and `received` in the Mermaid diagram while retaining unique diagram node IDs.
- Specify HTTP 422 for same-warehouse transfers in both the domain decisions and acceptance criteria. Reject the command before the database CHECK constraint, without inventory, operation, or event changes.
- Remove the main-publication exception so every workflow description preserves the user-owned handoff.

The existing controller rejects every changed head after approval, so the supporting change adds `--reopen-approved` and PowerShell `-ReopenApproved`. The flag is review-only and requires explicit user authorization for corrections in the same stage. It preserves the original base, prior review artifacts, exact published-head validation, and the shared limit of three correction rounds. A successful reopening consumes the next round; a blocked transport retry for the reserved head uses the normal review command without consuming another round. Ordinary review still rejects a changed approved head without the flag.

This is an additional review-workflow change outside the initial five-document candidate. On 2026-11-14, after reviewing the prepared scope and the passing 100-test result, the user explicitly approved including this controller support and continuing publication and review. It changes `scripts/review/local_review.py`, `scripts/Review-Stage.ps1`, and `tests/review/test_local_review.py`, with corresponding updates to AGENTS and the review documentation. It does not change the remote reviewer policy, application behavior, database, or product architecture. The unrelated local remote-launcher/model-setting changes remain excluded.

The HTTP 422 clarification must be carried into the later API implementation and tests when their stage is authorized; it is not an authorization to edit those drafts now.

### Follow-up verification

- `python -m unittest discover -s tests/review -v`: 100 tests passed in 72.040 seconds in an isolated export of the candidate Git index. This includes 13 new reopening regressions and excludes unrelated local runner edits and all untracked application drafts.
- Focused isolated controller checks: `python -m unittest discover -s tests/review -p test_local_review.py -v`: 42 tests passed in 42.899 seconds.
- PowerShell wrapper syntax passed. PowerShell 7 rejected `-ReopenApproved` with `-Action Status` as BLOCKED/exit 3 without creating state. Windows PowerShell 5.1 passed the same check with explicit `-Repo`; its pre-existing default-parameter evaluation fails without that argument, before controller execution.
- Document reconciliation confirmed all three P3 corrections and preserved R01-R15/T01-T14 coverage. `git diff --cached --check` passed.
- PostgreSQL/application tests were not run: no application code is part of this candidate.

## Local clarification deferred to stage B publication

On 2026-11-14, the user requested resolving the remaining optional P3 from the iteration-1 review of `3bacd0dd53d94ebc38b474960ae374018e61199f`, while explicitly deferring its commit and publication to stage B and requesting no separate review for it.

The local domain and acceptance documents now specify that creating a transfer draft writes exactly one `transfer_created` history event in the same transaction as the draft operation. It records SKU, quantity, source, destination, UTC time, operation ID, and draft status without changing physical stock, reservations, or transit. An identical create replay adds no event; the full lifecycle has exactly three events: created, dispatched, and received. This resolves the ambiguity in T04/T13 without adding a product feature.

Verification for this local clarification: the two domain documents were reconciled against the plan's unchanged-stock draft semantics; the existing untracked service/test drafts were inspected read-only for compatibility, not executed or included in the PR; `git diff --check` passed. No additional Claude review, commit, push, or application test run is claimed for this clarification. The stage A PR remains at the previously approved published head. Carry these local edits into the next authorized stage candidate before its approval and review; stage B implementation has not started.

After the user accepted and merged stage A, this clarification was carried into the authorized stage B candidate. See `B_INFRASTRUCTURE.md` for its current publication boundary and verification evidence. Transfer command implementation and the corresponding behavioral checks remain stage C work.
