# Stage B: infrastructure and demonstration receipts

## Review range and scope

Stage: `stage-b-infrastructure`. Immutable base: `3bacd0dd53d94ebc38b474960ae374018e61199f`.

The owner accepted stage A and merged PR #1 into `main` at `72475b2caa993c7cb1069556658f8ed29dfef144`. Published `dev` remains at the stage A head; both commits have identical trees. The stage start command recorded that published `dev` base before importing or modifying application files. No branch publication is needed to record the new stage.

Stage B imports only configuration, PostgreSQL models and migration, the receipt service required by seed, explicit demo seed, a database-backed health endpoint, Docker Compose, dependency metadata, safety ignores, focused tests, and current-stage documentation. Transfer/FBS/FBO commands, warehouse lifecycle services, reporting, business HTTP routes, static assets, templates, and final submission materials remain outside this candidate.

## Decisions and corrections

- The receipt service owns no commit. Its caller combines the selected balance, completed operation, and history event in one transaction. Advisory locks protect creation keys; warehouse and balance row locks serialize receipts. Database constraints remain the final invariant checks.
- Demo seed uses stable catalog UUIDs and creation keys. Four positive receipts add 25 units; zero cells are derived from the full catalog cross product. A conflicting SKU with another ID is rejected atomically.
- Migration creates all five agreed tables, including the states needed by later stages. Schema tests of those states do not claim that their lifecycle services are implemented.
- Append-only history rejects UPDATE, DELETE, and TRUNCATE. The last protection closes a gap in the imported draft without changing the agreed accounting model.
- The deferred stage A P3 clarification is included in DECISIONS and ACCEPTANCE: a draft transfer has one `transfer_created` event without quantity changes, and an identical replay adds no event. Stage C must implement and test that behavior in T04/T13. No separate review is requested for the clarification.
- The initial full application draft is preserved outside this candidate. The health-only entry point, package metadata, pytest discovery, and README are scoped to B. Review-controller tests run separately from application tests.

## Candidate files

Infrastructure: `.dockerignore`, `.env.example`, `.gitignore`, `Dockerfile`, `compose.yaml`, `pyproject.toml`, `alembic.ini`, `README.md`.

Application: `app/__init__.py`, `app/config.py`, `app/db.py`, `app/main.py`, `app/models.py`, `app/seed.py`, `app/services/__init__.py`, `app/services/stock.py`.

Migration: `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_initial.py`.

Tests: `tests/__init__.py`, `tests/conftest.py`, `tests/test_constraints.py`, `tests/test_migrations.py`, `tests/test_seed.py`, `tests/test_receipts.py`, `tests/test_health.py`.

Contract and evidence: `docs/DECISIONS.md`, `docs/ACCEPTANCE.md`, `docs/stages/A_DOMAIN.md`, `docs/stages/B_INFRASTRUCTURE.md`.

Unrelated local reviewer-policy transport changes in `scripts/review/remote_review.py` and `tests/review/test_remote_review.py` are preserved and excluded. Existing untracked C-F files are neither built nor tested nor included in this stage.

## Verification evidence

The candidate was exported from a temporary Git index, leaving the real checkout index unchanged. Its tested tree was `872c593fd09c436245a3b24c9fc0f7d85881ae11`; the verification prose below was added after the run. The export contains the unchanged committed review infrastructure plus the explicit 30-file stage allowlist. It excludes future-stage untracked files, both unrelated local reviewer transport edits, private runtime settings, and original draft backups.

Application checks used the isolated Compose project `checkstock_stage_b_20261114`, a fresh named volume, loopback port 18081, and PostgreSQL 17.12 with timezone Etc/UTC. Its temporary runtime settings were outside the exported tree and were never printed or included in an image/review package. Docker Desktop was initially stopped and was started for these checks. The existing working/demo project and volumes were not modified.

Every Compose command below used explicit `-p`, `--env-file`, `--project-directory`, and `-f` arguments for that isolated export. Commands and observed results:

| Command/check | Observed result |
| --- | --- |
| `python scripts/review/local_review.py start --stage stage-b-infrastructure` | STARTED at the immutable base above; preserved tracked changes were restored byte-for-byte. |
| `git diff --cached --check` with a temporary candidate index | Passed; real index remained unchanged. |
| `docker compose config -q` | Passed without printing resolved settings. |
| `docker compose up --build -d --wait --wait-timeout 180` | Built successfully; app and PostgreSQL healthy. |
| `docker compose exec -T app alembic current` | `0001_initial (head)`. |
| Read all five tables through the running application's database session before seed | All empty; migration/startup did not create demo data. |
| `docker compose exec -T app python -m app.seed`, twice | Three products, two warehouses, six logical stock cells including two zeros, 25 physical units, zero reserved/transit, four receipt operations and four events. Complete snapshots including IDs and timestamps were identical after the second run. |
| `docker compose exec -T app pytest -q` | **83 passed in 7.76s**, on real PostgreSQL. |
| `docker compose exec -T app alembic check` | `No new upgrade operations detected.` |
| `python -m unittest discover -s tests/review -v` in the candidate export | **100 passed in 73.206s**, separately on the host. |
| Compare the full demo snapshot after all tests; query temporary schemas | All demo rows unchanged; no test schemas left behind. |
| `docker compose restart db`, then `docker compose up -d --wait --wait-timeout 120` | Both services healthy; every demo row, ID, timestamp and quantity preserved. |
| `docker compose restart app`, then the same wait command | Healthy; repeated startup/migration did not reseed or change demo rows. |
| `GET http://127.0.0.1:18081/health` before and after restarts | HTTP 200, `{"status":"ok"}`. |
| Inspect the running application filesystem, process UID, and routes | Non-root; only `/health`; no later API/templates/schemas or review tests in the image. |

The application suite covers empty migration, model/schema consistency, safe downgrade/upgrade, constraints and restrictive FKs, immutable history, strict receipt validation, local balance updates, identity conflicts, replay after archive, balance overflow, mid-operation and mid-seed rollback, concurrent seeds, and concurrent receipts against a missing balance. The health checks cover real PostgreSQL success and a sanitized database-error response. A separate review of test sufficiency found no blocking B coverage gap.

The initial export command needed a PowerShell argument-quoting correction before the archive was created; it did not alter the real index or candidate contents. No application test failures were observed. No C-F tests or full business/UI acceptance are claimed. Independent Claude review, commit, and push are pending user approval.

## Next-stage boundary

This candidate has passed the checks above and still requires user approval before commit, publication to `dev`, and independent Claude review of the complete stage. Only after APPROVED and user-owned main integration can stage C begin. Its scope must use the updated transfer event contract and extend these transaction primitives instead of importing the old full draft without reconciliation.

Proposed commit message: `feat: add stage B infrastructure and idempotent demo receipts`.
