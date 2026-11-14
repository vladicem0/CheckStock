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

The human approval and main-integration gates are an explicit addition requested by the user on 2026-11-14, outside the original application's A-F feature scope. They change the delivery process, not the product architecture or functionality. The existing reviewer controller, external Claude policy, result schema, and correction limit are unchanged.

## Verification performed for this candidate

- `python scripts/review/local_review.py start --stage stage-a-domain-contract`: STARTED; local and published dev matched the immutable base above. No reviewer was invoked by this action.
- Document coverage assertions: all R01-R15 and T01-T14 entries are present; state names, stock/reserve invariants, idempotency, and row locking are present in the decisions document.
- Read-only reconciliation of the domain documents against the implementation contract and review of the staged-import boundaries.
- `python -m unittest discover -s tests/review -v`: 87 tests passed in 49.798 seconds from an isolated export of the exact candidate Git index, excluding all later-stage untracked application files and unrelated local runner changes. This validates the existing review integration, not the warehouse application.
- `git diff --check`: passed for the tracked process-documentation changes.
- Read-only SSH preflight to `dev@vpn`: the installed review entry point is executable and the external reviewer policy is readable. No policy contents, credentials, or application files were transmitted as a review request.

No application code changed, so the historical PostgreSQL run was not repeated or claimed as current stage evidence. No Claude review, stage commit, or publication was performed during candidate preparation. Approval and the exact reviewed revision must be established through the documented handoffs.

## Later-stage preparation issues

Stage B must validate an isolated candidate tree with a health-only entry point. The existing complete local main module imports later API/UI components and cannot be committed unchanged at that boundary. Future untracked code/tests must not leak into the build.

Application pytest discovery must also be separated from the existing `tests/review` suite when preparing stage B: that suite invokes Git, which is not installed in the application image. Continue running the review suite separately through its documented host unittest command. This preparation item is recorded here and has not been implemented ahead of the user's stage handoff.
