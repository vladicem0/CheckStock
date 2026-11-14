"""HTTP reports establish a repeatable PostgreSQL snapshot before reading data."""
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, text

from app.db import get_session
from app.main import app
from app.services.operations import reserve_fbs, ship_fbs
from app.services.warehouses import rename_warehouse
from tests.helpers import NORTH, P101, run


@pytest.fixture
def report_client(db):
    def session_override():
        with db() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.parametrize("path", ["/api/stocks", "/api/operations", "/api/history", "/api/warehouses"])
def test_every_http_report_uses_actual_repeatable_read_before_data_queries(db, report_client, path):
    engine = db.kw["bind"]
    observed_isolation = []

    def check_isolation(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            observed_isolation.append(connection.exec_driver_sql("SHOW transaction_isolation").scalar_one())

    event.listen(engine, "before_cursor_execute", check_isolation)
    try:
        response = report_client.get(path)
        assert response.status_code == 200
        assert observed_isolation and set(observed_isolation) == {"repeatable read"}
    finally:
        event.remove(engine, "before_cursor_execute", check_isolation)


def test_stock_report_keeps_one_snapshot_during_parallel_shipment(db, report_client):
    operation = run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=4,
                    customer="Клиент", idempotency_key="snapshot")
    engine = db.kw["bind"]
    triggered = False
    backend_pids = []

    def ship_in_separate_transaction():
        with db.begin() as session:
            backend_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ship_fbs(session, operation.id)

    def ship_after_first_report_query(connection, cursor, statement, parameters, context, executemany):
        nonlocal triggered
        if not triggered and "FROM products" in statement and "SELECT" in statement:
            triggered = True
            backend_pids.append(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(ship_in_separate_transaction).result(timeout=10)

    event.listen(engine, "after_cursor_execute", ship_after_first_report_query)
    try:
        response = report_client.get("/api/stocks")
        assert response.status_code == 200
        data = response.json()
        assert triggered, "The test must actually overlap a report and a shipment"
        assert len(set(backend_pids)) == 2
        assert data["totals"]["on_hand"] == 25
        assert data["totals"]["reserved_fbs"] == 4
        assert data["totals"]["transit_fbs"] == 0
        current = report_client.get("/api/stocks").json()["totals"]
        assert current["on_hand"] == 21
        assert current["reserved_fbs"] == 0
        assert current["transit_fbs"] == 4
    finally:
        event.remove(engine, "after_cursor_execute", ship_after_first_report_query)


def test_operation_report_does_not_mix_old_status_with_new_warehouse_name(db, report_client):
    operation = run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=4,
                    customer="Клиент", idempotency_key="operation-snapshot")
    engine = db.kw["bind"]
    triggered = False
    backend_pids = []

    def change_in_separate_transaction():
        with db.begin() as session:
            backend_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ship_fbs(session, operation.id)
            rename_warehouse(session, NORTH, "Север после отправки")

    def change_after_operation_query(connection, cursor, statement, parameters, context, executemany):
        nonlocal triggered
        if not triggered and "FROM operations" in statement and "SELECT" in statement:
            triggered = True
            backend_pids.append(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(change_in_separate_transaction).result(timeout=10)

    event.listen(engine, "after_cursor_execute", change_after_operation_query)
    try:
        response = report_client.get("/api/operations")
        assert response.status_code == 200
        assert triggered and len(set(backend_pids)) == 2
        reported = next(item for item in response.json() if item["id"] == str(operation.id))
        assert (reported["status"], reported["source_name"]) == ("reserved", "Север")
        current_response = report_client.get("/api/operations")
        assert current_response.status_code == 200
        current = next(item for item in current_response.json() if item["id"] == str(operation.id))
        assert (current["status"], current["source_name"]) == ("shipped", "Север после отправки")
    finally:
        event.remove(engine, "after_cursor_execute", change_after_operation_query)
