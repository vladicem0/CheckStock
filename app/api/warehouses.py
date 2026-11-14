"""Warehouse HTTP boundary; delete means archive."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from app.api import SessionDep
from app.models import Warehouse
from app.schemas import WarehouseInput, WarehouseView
from app.services.inventory import warehouse_view
from app.services.warehouses import archive_warehouse, create_warehouse, rename_warehouse

router = APIRouter(prefix="/api/warehouses", tags=["Склады"])


@router.get(
    "",
    response_model=list[WarehouseView],
    summary="Все ФФ, включая архивные",
    description="Архивные склады сохраняют ID и остаются видимы вместе с активными.",
)
def warehouses(session: SessionDep) -> list[dict[str, Any]]:
    with session.begin():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        return [warehouse_view(item) for item in session.scalars(
            select(Warehouse).order_by(Warehouse.created_at, Warehouse.id)
            .execution_options(populate_existing=True)
        )]


@router.post(
    "",
    response_model=WarehouseView,
    summary="Создать ФФ",
    description="Создаёт активный склад с постоянным ID и нулевыми начальными остатками.",
)
def create(payload: WarehouseInput, session: SessionDep) -> dict[str, Any]:
    with session.begin():
        warehouse = create_warehouse(session, payload.name)
        session.flush()
        return warehouse_view(warehouse)


@router.patch(
    "/{warehouse_id}",
    response_model=WarehouseView,
    summary="Переименовать ФФ",
    description="Меняет текущее название, сохраняя ID, остатки и названия в прошлых событиях.",
)
def rename(warehouse_id: UUID, payload: WarehouseInput, session: SessionDep) -> dict[str, Any]:
    with session.begin():
        warehouse = rename_warehouse(session, warehouse_id, payload.name)
        session.flush()
        return warehouse_view(warehouse)


@router.delete(
    "/{warehouse_id}",
    response_model=WarehouseView,
    summary="Архивировать ФФ без удаления товара",
    description=(
        "Архивирует вместо физического удаления. Остатки и история сохраняются; повтор безопасен. "
        "Можно завершить прежние операции и вывести остатки перемещением на активный ФФ."
    ),
)
def archive(warehouse_id: UUID, session: SessionDep) -> dict[str, Any]:
    with session.begin():
        warehouse = archive_warehouse(session, warehouse_id)
        session.flush()
        return warehouse_view(warehouse)
