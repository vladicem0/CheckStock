"""Unexpected HTTP failures are sanitized without committing partial movements."""

import logging

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import update
from sqlalchemy.exc import (
    DBAPIError, IntegrityError, InterfaceError, InvalidRequestError,
    OperationalError, ProgrammingError, SQLAlchemyError, TimeoutError,
)

from app.db import get_session
from app.main import app
from app.models import StockBalance
from app.services import operations
from tests.helpers import NORTH, P101, snapshot


def test_unexpected_failure_returns_safe_500_and_rolls_back(db, monkeypatch):
    def session_override():
        with db() as session:
            yield session

    def fail_after_flush(session, *args, **kwargs):
        session.flush()
        raise RuntimeError("internal-diagnostic-marker")

    before = snapshot(db)
    monkeypatch.setattr(operations, "add_event", fail_after_flush)
    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/fbs/reservations", json={
                "product_id": str(P101), "warehouse_id": str(NORTH),
                "qty": 2, "customer": "Клиент", "idempotency_key": "unexpected-http-error",
            })
        assert response.status_code == 500
        assert response.json() == {"detail": "Внутренняя ошибка сервера"}
        assert "internal-diagnostic-marker" not in response.text
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert snapshot(db) == before


@pytest.fixture
def error_client(db):
    def session_override():
        with db() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.parametrize("error_type,status_code", [
    (OperationalError, 503), (InterfaceError, 503), (TimeoutError, 503),
    (DBAPIError, 503), (InvalidRequestError, 500), (IntegrityError, 500),
    (ProgrammingError, 500), (SQLAlchemyError, 500),
])
def test_database_failures_are_classified_logged_and_rolled_back(
    db, error_client, monkeypatch, caplog, error_type, status_code,
):
    if issubclass(error_type, DBAPIError):
        error = error_type(
            "private-statement-marker", {"private-parameter-marker": "private-value-marker"},
            RuntimeError("private-driver-marker"),
            connection_invalidated=error_type is DBAPIError,
        )
    else:
        error = error_type("private-diagnostic-marker")

    def fail_after_flush(session, *args, **kwargs):
        session.flush()
        raise error

    before = snapshot(db)
    monkeypatch.setattr(operations, "add_event", fail_after_flush)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        response = error_client.post("/api/fbs/reservations", json={
            "product_id": str(P101), "warehouse_id": str(NORTH), "qty": 2,
            "customer": "private-customer-marker", "idempotency_key": "private-key-marker",
        })
    assert response.status_code == status_code
    assert response.json() == {"detail": (
        "База данных временно недоступна" if status_code == 503 else "Внутренняя ошибка сервера"
    )}
    records = [record for record in caplog.records if record.name == "app.main"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert f"type={error_type.__name__}" in message
    assert f"status={status_code}" in message
    assert "fail_after_flush" in message
    assert "private-" not in response.text
    assert "private-" not in caplog.text
    assert snapshot(db) == before


def test_real_constraint_failure_is_logged_as_500_and_rolls_back(
    db, error_client, monkeypatch, caplog,
):
    def violate_constraint_after_flush(session, *args, **kwargs):
        session.flush()
        session.execute(update(StockBalance).where(
            StockBalance.product_id == P101, StockBalance.warehouse_id == NORTH,
        ).values(on_hand=-1))

    before = snapshot(db)
    monkeypatch.setattr(operations, "add_event", violate_constraint_after_flush)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        response = error_client.post("/api/fbs/reservations", json={
            "product_id": str(P101), "warehouse_id": str(NORTH), "qty": 2,
            "customer": "Клиент", "idempotency_key": "real-constraint-failure",
        })
    assert response.status_code == 500
    assert response.json() == {"detail": "Внутренняя ошибка сервера"}
    assert "type=IntegrityError status=500" in caplog.text
    assert "violate_constraint_after_flush" in caplog.text
    assert "UPDATE stock_balances" not in caplog.text
    assert snapshot(db) == before
