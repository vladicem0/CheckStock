"""Allow delivered FBS shipments without rewriting existing accounting history.

Revision ID: 0002_fbs_delivery
Revises: 0001_initial
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_fbs_delivery"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_operation_status", "operations", type_="check")
    op.create_check_constraint(
        "ck_operation_status",
        "operations",
        "(kind = 'receipt' AND status = 'completed') OR "
        "(kind = 'transfer' AND status IN ('draft','in_transit','received')) OR "
        "(kind = 'fbs' AND status IN ('reserved','shipped','delivered','cancelled')) OR "
        "(kind = 'fbo' AND status IN ('in_transit','received'))",
    )


def downgrade() -> None:
    # The lock closes the gap between the precondition and constraint replacement.
    # SQL also keeps the refusal effective when this migration is rendered offline.
    op.execute("LOCK TABLE operations IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM operations WHERE status = 'delivered') THEN
                RAISE EXCEPTION 'Cannot downgrade: delivered FBS shipments must be preserved';
            END IF;
        END;
        $$
    """)
    op.drop_constraint("ck_operation_status", "operations", type_="check")
    op.create_check_constraint(
        "ck_operation_status",
        "operations",
        "(kind = 'receipt' AND status = 'completed') OR "
        "(kind = 'transfer' AND status IN ('draft','in_transit','received')) OR "
        "(kind = 'fbs' AND status IN ('reserved','shipped','cancelled')) OR "
        "(kind = 'fbo' AND status IN ('in_transit','received'))",
    )
