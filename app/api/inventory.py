"""Inventory, operation and history reports in request-owned snapshots."""

from typing import Any

from fastapi import APIRouter

from app.api import SessionDep
from app.schemas import InventoryView, OperationView, StockEventView
from app.services.inventory import get_inventory, list_history, list_operations

router = APIRouter(prefix="/api", tags=["Учёт"])


@router.get(
    "/stocks",
    response_model=InventoryView,
    summary="Остатки и три категории транзита",
    description=(
        "Все товары × все ФФ, включая нули и архивные склады. "
        "Физический остаток уже включает резерв FBS; доступно = физически − резерв. "
        "Остатки и транзит читаются из одного снимка PostgreSQL."
    ),
)
def stocks(session: SessionDep) -> dict[str, Any]:
    with session.begin():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        return get_inventory(session)


@router.get(
    "/operations",
    response_model=list[OperationView],
    summary="Все складские операции",
    description="Текущие статусы и названия складов; новые операции идут первыми.",
)
def operations(session: SessionDep) -> list[dict[str, Any]]:
    with session.begin():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        return list_operations(session)


@router.get(
    "/history",
    response_model=list[StockEventView],
    summary="Неизменяемая история событий",
    description=(
        "SKU, количество, маршрут, UTC-время и статус на момент каждого действия. "
        "Переименование и архивация складов не меняют прошлые записи; новые события идут первыми."
    ),
)
def history(session: SessionDep) -> list[dict[str, Any]]:
    with session.begin():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        return list_history(session)
