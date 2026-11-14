"""Warehouse creation, rename and soft archive in the caller's transaction."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Warehouse
from app.services.stock import DomainError, lock_warehouses, require_postgres_text


def _name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 200
    ):
        raise DomainError("Название склада должно содержать от 1 до 200 символов", 422)
    require_postgres_text(value)
    return value.strip()


def create_warehouse(session: Session, name: str) -> Warehouse:
    warehouse = Warehouse(name=_name(name))
    session.add(warehouse)
    session.flush()
    return warehouse


def rename_warehouse(session: Session, warehouse_id: UUID, name: str) -> Warehouse:
    name = _name(name)
    warehouse = lock_warehouses(session, warehouse_id)[warehouse_id]
    warehouse.name = name
    session.flush()
    return warehouse


def archive_warehouse(session: Session, warehouse_id: UUID) -> Warehouse:
    """Archive once while preserving balances, identity and existing operations."""
    warehouse = lock_warehouses(session, warehouse_id)[warehouse_id]
    if warehouse.archived_at is None:
        warehouse.archived_at = datetime.now(timezone.utc)
        session.flush()
    return warehouse
