"""Create warehouse accounting tables and immutable stock history.

Revision ID: 0001_initial
Revises:
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "stock_balances",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("on_hand", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved_fbs", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("on_hand >= 0", name="ck_balance_physical"),
        sa.CheckConstraint("reserved_fbs >= 0 AND reserved_fbs <= on_hand", name="ck_balance_reserved"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("product_id", "warehouse_id"),
    )
    op.create_table(
        "operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("source_warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("destination_warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("external_party", sa.String(200), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("qty > 0", name="ck_operation_qty"),
        sa.CheckConstraint(
            "(kind = 'receipt' AND status = 'completed') OR "
            "(kind = 'transfer' AND status IN ('draft','in_transit','received')) OR "
            "(kind = 'fbs' AND status IN ('reserved','shipped','cancelled')) OR "
            "(kind = 'fbo' AND status IN ('in_transit','received'))",
            name="ck_operation_status",
        ),
        sa.CheckConstraint(
            "(kind = 'receipt' AND source_warehouse_id IS NULL "
            "AND destination_warehouse_id IS NOT NULL AND external_party IS NULL) OR "
            "(kind = 'transfer' AND source_warehouse_id IS NOT NULL "
            "AND destination_warehouse_id IS NOT NULL "
            "AND source_warehouse_id <> destination_warehouse_id AND external_party IS NULL) OR "
            "(kind IN ('fbs','fbo') AND source_warehouse_id IS NOT NULL "
            "AND destination_warehouse_id IS NULL AND external_party IS NOT NULL "
            "AND length(trim(external_party)) > 0)",
            name="ck_operation_route",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["destination_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_operations_kind_status", "operations", ["kind", "status"])
    op.create_index("ix_operations_created", "operations", ["created_at"])
    op.create_index("ix_operations_product_id", "operations", ["product_id"])
    op.create_index("ix_operations_source_warehouse_id", "operations", ["source_warehouse_id"])
    op.create_index("ix_operations_destination_warehouse_id", "operations", ["destination_warehouse_id"])
    op.create_table(
        "stock_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("operation_status", sa.String(20), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("from_warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("to_warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("from_name", sa.String(200), nullable=False),
        sa.Column("to_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("qty > 0", name="ck_event_qty"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["from_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "event_type", name="uq_event_transition"),
    )
    op.create_index("ix_events_created", "stock_events", ["created_at"])
    op.create_index("ix_stock_events_product_id", "stock_events", ["product_id"])
    op.execute("""
        CREATE FUNCTION checkstock_reject_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'stock_events is append-only'
                USING ERRCODE = '23514';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER stock_events_append_only
        BEFORE UPDATE OR DELETE ON stock_events
        FOR EACH ROW EXECUTE FUNCTION checkstock_reject_event_mutation()
    """)
    op.execute("""
        CREATE TRIGGER stock_events_no_truncate
        BEFORE TRUNCATE ON stock_events
        FOR EACH STATEMENT EXECUTE FUNCTION checkstock_reject_event_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER stock_events_no_truncate ON stock_events")
    op.execute("DROP TRIGGER stock_events_append_only ON stock_events")
    op.execute("DROP FUNCTION checkstock_reject_event_mutation()")
    op.drop_table("stock_events")
    op.drop_table("operations")
    op.drop_table("stock_balances")
    op.drop_table("warehouses")
    op.drop_table("products")
