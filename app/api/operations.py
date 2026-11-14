"""Validated commands, one transaction per HTTP mutation."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.api import SessionDep
from app.models import Operation
from app.schemas import FboInput, FbsInput, OperationView, ReceiptInput, TransferInput
from app.services import operations
from app.services.inventory import operation_view
from app.services.stock import receive_stock

router = APIRouter(prefix="/api", tags=["Операции"])


def _execute(
    session: Session,
    command: Callable[..., Operation],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Commit the whole service command before returning its serialized state."""
    with session.begin():
        operation = command(session, *args, **kwargs)
        session.flush()
        return operation_view(session, operation)


@router.post(
    "/receipts",
    response_model=OperationView,
    summary="Приход товара на ФФ",
    description=(
        "Увеличивает физический остаток выбранного SKU/ФФ и записывает историю. "
        "Одинаковый повтор с тем же ключом возвращает прежнюю операцию без нового прихода."
    ),
)
def receipt(payload: ReceiptInput, session: SessionDep) -> dict[str, Any]:
    return _execute(session, receive_stock, **payload.model_dump())


@router.post(
    "/transfers",
    response_model=OperationView,
    summary="Создать заявку на перемещение",
    description=(
        "Создаёт draft и одно событие transfer_created без изменения остатка, резерва или транзита. "
        "Наличие товара проверяется при отправке. Источник и активный получатель должны различаться."
    ),
)
def transfer(payload: TransferInput, session: SessionDep) -> dict[str, Any]:
    return _execute(session, operations.create_transfer, **payload.model_dump())


@router.post(
    "/transfers/{operation_id}/dispatch",
    response_model=OperationView,
    summary="Отправить перемещение",
    description=(
        "Списывает только доступный остаток источника и переводит товар в межскладской транзит. "
        "Повтор безопасен, в том числе после приёмки."
    ),
)
def dispatch_transfer(operation_id: UUID, session: SessionDep) -> dict[str, Any]:
    return _execute(session, operations.dispatch_transfer, operation_id)


@router.post(
    "/transfers/{operation_id}/receive",
    response_model=OperationView,
    summary="Принять перемещение",
    description="Завершает транзит и зачисляет товар получателю. Приёмка черновика запрещена.",
)
def receive_transfer(operation_id: UUID, session: SessionDep) -> dict[str, Any]:
    return _execute(session, operations.receive_transfer, operation_id)


@router.post(
    "/fbs/reservations",
    response_model=OperationView,
    summary="Зарезервировать товар FBS",
    description=(
        "Резервирует доступное количество на выбранном активном ФФ, не уменьшая физический остаток. "
        "Запас другого склада не используется автоматически."
    ),
)
def reserve_fbs(payload: FbsInput, session: SessionDep) -> dict[str, Any]:
    return _execute(session, operations.reserve_fbs, **payload.model_dump())


@router.post(
    "/fbs/{operation_id}/ship",
    response_model=OperationView,
    summary="Отправить FBS клиенту",
    description="Списывает физический товар и резерв этой операции; товар учитывается в пути к клиенту.",
)
def ship_fbs(operation_id: UUID, session: SessionDep) -> dict[str, Any]:
    return _execute(session, operations.ship_fbs, operation_id)


@router.post(
    "/fbs/{operation_id}/cancel",
    response_model=OperationView,
    summary="Отменить резерв FBS",
    description="Освобождает резерв до отправки. Отмена отправленного заказа запрещена.",
)
def cancel_fbs(operation_id: UUID, session: SessionDep) -> dict[str, Any]:
    return _execute(session, operations.cancel_fbs, operation_id)


@router.post(
    "/fbo/shipments",
    response_model=OperationView,
    summary="Отправить FBO маркетплейсу",
    description="Списывает доступный товар с выбранного активного ФФ и сразу создаёт транзит FBO.",
)
def ship_fbo(payload: FboInput, session: SessionDep) -> dict[str, Any]:
    return _execute(session, operations.ship_fbo, **payload.model_dump())


@router.post(
    "/fbo/{operation_id}/receive",
    response_model=OperationView,
    summary="Подтвердить приёмку FBO маркетплейсом",
    description="Завершает транзит FBO; товар не зачисляется обратно на ФФ. Повтор не меняет учёт.",
)
def receive_fbo(operation_id: UUID, session: SessionDep) -> dict[str, Any]:
    return _execute(session, operations.receive_fbo, operation_id)
