"""Persistent accounting state; all stock movements are recorded as operations."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    sku: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockBalance(Base):
    __tablename__ = "stock_balances"
    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="ck_balance_physical"),
        CheckConstraint(
            "reserved_fbs >= 0 AND reserved_fbs <= on_hand",
            name="ck_balance_reserved",
        ),
    )
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), primary_key=True
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), primary_key=True
    )
    on_hand: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reserved_fbs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved_fbs


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_operation_qty"),
        CheckConstraint(
            "(kind = 'receipt' AND status = 'completed') OR "
            "(kind = 'transfer' AND status IN ('draft','in_transit','received')) OR "
            "(kind = 'fbs' AND status IN ('reserved','shipped','delivered','cancelled')) OR "
            "(kind = 'fbo' AND status IN ('in_transit','received'))",
            name="ck_operation_status",
        ),
        CheckConstraint(
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
        Index("ix_operations_kind_status", "kind", "status"),
        Index("ix_operations_created", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    qty: Mapped[int] = mapped_column(Integer)
    source_warehouse_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), index=True
    )
    destination_warehouse_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), index=True
    )
    external_party: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StockEvent(Base):
    __tablename__ = "stock_events"
    __table_args__ = (
        UniqueConstraint("operation_id", "event_type", name="uq_event_transition"),
        CheckConstraint("qty > 0", name="ck_event_qty"),
        Index("ix_events_created", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("operations.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(40))
    operation_status: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    sku: Mapped[str] = mapped_column(String(80))
    qty: Mapped[int] = mapped_column(Integer)
    from_warehouse_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT")
    )
    to_warehouse_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT")
    )
    from_name: Mapped[str] = mapped_column(String(200))
    to_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
