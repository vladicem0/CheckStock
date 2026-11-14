"""The Stage B health endpoint checks PostgreSQL and never seeds the database."""

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db import get_session
from app.main import app


def test_health_uses_postgresql_without_creating_demo_data(empty_db, stock_snapshot):
    def test_session():
        with empty_db() as session:
            yield session

    before = stock_snapshot()
    assert all(not rows for rows in before.values())
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert stock_snapshot() == before


def test_health_reports_unavailable_database_without_internal_diagnostic(empty_db, monkeypatch, caplog):
    def failing_execute(*args, **kwargs):
        raise OperationalError("SELECT 1", None, RuntimeError("internal-diagnostic-marker"))

    def failing_session():
        with empty_db() as session:
            monkeypatch.setattr(session, "execute", failing_execute)
            yield session

    app.dependency_overrides[get_session] = failing_session
    try:
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 503
        assert response.json() == {"detail": "База данных недоступна"}
        assert "internal-diagnostic-marker" not in response.text
        assert "type=OperationalError status=503" in caplog.text
        assert "failing_execute" in caplog.text
        assert "internal-diagnostic-marker" not in caplog.text
    finally:
        app.dependency_overrides.pop(get_session, None)
