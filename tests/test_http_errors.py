"""Unexpected HTTP failures are sanitized without committing partial movements."""

from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
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
