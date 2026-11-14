"""HTTP contract checks against migrated, isolated real PostgreSQL schemas."""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import get_session
from app.main import app
from app.services import operations, stock as stock_services
from app.services.stock import DomainError
from tests.helpers import NORTH, SOUTH, P101, P102, P103, snapshot as database_snapshot


@pytest.fixture
def client(db):
    def isolated_session():
        with db() as session:
            yield session

    app.dependency_overrides[get_session] = isolated_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


def get(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def post(client, path, payload=None):
    response = client.post(path, json=payload) if payload is not None else client.post(path)
    assert response.status_code == 200, response.text
    return response.json()


def stock(client, product_id=P101, warehouse_id=NORTH):
    cell = next(cell for cell in get(client, "/api/stocks")["balances"]
                if cell["product_id"] == str(product_id) and cell["warehouse_id"] == str(warehouse_id))
    return cell["on_hand"], cell["reserved_fbs"], cell["available"]


def fbs_payload(qty=4, key="fbs", product_id=P101, warehouse_id=NORTH):
    return dict(product_id=str(product_id), warehouse_id=str(warehouse_id), qty=qty,
                customer="Клиент", idempotency_key=key)


def transfer_payload(qty=3, key="transfer", product_id=P101, source=NORTH, destination=SOUTH):
    return dict(product_id=str(product_id), source_warehouse_id=str(source),
                destination_warehouse_id=str(destination), qty=qty, idempotency_key=key)


def receipt_payload(qty=1, key="receipt", product_id=P101, warehouse_id=NORTH):
    return dict(product_id=str(product_id), warehouse_id=str(warehouse_id), qty=qty, idempotency_key=key)


def fbo_payload(qty=2, key="fbo", product_id=P101, warehouse_id=NORTH):
    return dict(product_id=str(product_id), warehouse_id=str(warehouse_id), qty=qty,
                marketplace="Маркетплейс", idempotency_key=key)


CREATE_ROUTES = [
    ("/api/receipts", receipt_payload),
    ("/api/transfers", transfer_payload),
    ("/api/fbs/reservations", fbs_payload),
    ("/api/fbo/shipments", fbo_payload),
]

TRANSITION_ROUTES = [
    "/api/transfers/{id}/dispatch", "/api/transfers/{id}/receive",
    "/api/fbs/{id}/ship", "/api/fbs/{id}/cancel", "/api/fbs/{id}/deliver", "/api/fbo/{id}/receive",
]


def http_snapshot(client):
    return {path: get(client, path) for path in ("/api/stocks", "/api/history", "/api/operations")}


def test_api_initial_matrix_includes_zero_cells_and_three_transit_groups(client):
    data = get(client, "/api/stocks")
    assert len(data["products"]) == 3
    assert len(data["warehouses"]) == 2
    assert len(data["balances"]) == 6
    assert data["totals"] == dict(on_hand=25, reserved_fbs=0, available=25,
                                  transit_ff=0, transit_fbo=0, transit_fbs=0)
    assert data["transit"] == {"ff": [], "fbo": [], "fbs": []}
    assert stock(client, P103, NORTH) == (0, 0, 0)
    assert stock(client, P102, SOUTH) == (0, 0, 0)


def test_api_t02_fbs_reserve_ship_and_repeat(client):
    operation = post(client, "/api/fbs/reservations", fbs_payload())
    operation_id = operation["id"]
    assert operation["kind"] == "fbs" and operation["status"] == "reserved"
    assert stock(client) == (10, 4, 6)
    assert post(client, f"/api/fbs/{operation_id}/ship")["status"] == "shipped"
    assert stock(client) == (6, 0, 6)
    data = get(client, "/api/stocks")
    assert data["totals"]["transit_fbs"] == 4
    assert data["transit"]["fbs"][0]["id"] == operation_id
    before = http_snapshot(client)
    assert post(client, f"/api/fbs/{operation_id}/ship")["id"] == operation_id
    assert http_snapshot(client) == before
    assert client.post(f"/api/fbs/{operation_id}/cancel").status_code == 409
    assert http_snapshot(client) == before


def test_api_t03_fbs_cancel_and_reject_shipping(client):
    operation = post(client, "/api/fbs/reservations", fbs_payload(qty=3))
    operation_id = operation["id"]
    assert post(client, f"/api/fbs/{operation_id}/cancel")["status"] == "cancelled"
    assert stock(client) == (10, 0, 10)
    before = http_snapshot(client)
    post(client, f"/api/fbs/{operation_id}/cancel")
    assert client.post(f"/api/fbs/{operation_id}/ship").status_code == 409
    assert http_snapshot(client) == before


def test_api_fbs_delivery_finishes_transit_without_touching_other_reserves(client, db):
    payload = fbs_payload(key="delivery-http")
    operation_id = post(client, "/api/fbs/reservations", payload)["id"]
    post(client, f"/api/fbs/{operation_id}/ship")
    post(client, "/api/fbs/reservations", fbs_payload(qty=1, key="other-reservation"))
    before = get(client, "/api/stocks")
    history_before = get(client, "/api/history")
    assert stock(client) == (6, 1, 5)
    assert before["totals"]["transit_fbs"] == 4

    delivered = post(client, f"/api/fbs/{operation_id}/deliver")
    assert (delivered["id"], delivered["kind"], delivered["status"], delivered["qty"]) == (
        operation_id, "fbs", "delivered", 4,
    )
    after = get(client, "/api/stocks")
    assert after["balances"] == before["balances"]
    assert after["totals"] == dict(before["totals"], transit_fbs=0)
    assert after["transit"] == {"ff": [], "fbo": [], "fbs": []}
    assert next(item for item in get(client, "/api/operations") if item["id"] == operation_id) == delivered
    history = get(client, "/api/history")
    old_ids = {item["id"] for item in history_before}
    new_events = [item for item in history if item["id"] not in old_ids]
    assert len(new_events) == 1
    event = new_events[0]
    assert (event["operation_id"], event["event_type"], event["operation_status"], event["qty"]) == (
        operation_id, "fbs_delivered", "delivered", 4,
    )
    assert event["from_name"] == "Север" and event["to_name"] == "Клиент"
    assert event["from_warehouse_id"] == str(NORTH) and event["to_warehouse_id"] is None
    assert [item for item in history if item["id"] in old_ids] == history_before

    rows, http_state = database_snapshot(db), http_snapshot(client)
    assert post(client, f"/api/fbs/{operation_id}/deliver") == delivered
    assert post(client, f"/api/fbs/{operation_id}/ship") == delivered
    assert post(client, "/api/fbs/reservations", payload) == delivered
    assert client.post(f"/api/fbs/{operation_id}/cancel").status_code == 409
    assert database_snapshot(db) == rows
    assert http_snapshot(client) == http_state


@pytest.mark.parametrize("status", ["reserved", "cancelled"])
def test_api_fbs_delivery_rejects_unshipped_operations_atomically(client, db, status):
    operation = post(client, "/api/fbs/reservations", fbs_payload())
    if status == "cancelled":
        post(client, f"/api/fbs/{operation['id']}/cancel")
    rows, before = database_snapshot(db), http_snapshot(client)
    response = client.post(f"/api/fbs/{operation['id']}/deliver")
    assert response.status_code == 409
    assert isinstance(response.json()["detail"], str)
    assert database_snapshot(db) == rows
    assert http_snapshot(client) == before


def test_api_fbs_delivery_failure_rolls_back_status_and_inserted_event(client, db, monkeypatch):
    operation = post(client, "/api/fbs/reservations", fbs_payload())
    post(client, f"/api/fbs/{operation['id']}/ship")
    original_add_event = operations.add_event
    before = database_snapshot(db)

    def fail_after_delivery_event(session, *args, **kwargs):
        original_add_event(session, *args, **kwargs)
        session.flush()
        raise OperationalError("private-delivery-statement", None, RuntimeError("private-delivery-error"))

    monkeypatch.setattr(operations, "add_event", fail_after_delivery_event)
    response = client.post(f"/api/fbs/{operation['id']}/deliver")
    assert response.status_code == 503
    assert "private-delivery" not in response.text
    assert database_snapshot(db) == before
    assert get(client, "/api/stocks")["totals"]["transit_fbs"] == 4


def test_api_t04_transfer_all_steps_and_repeated_confirmations(client):
    operation = post(client, "/api/transfers", transfer_payload())
    operation_id = operation["id"]
    assert operation["status"] == "draft"
    assert stock(client) == (10, 0, 10)
    assert stock(client, warehouse_id=SOUTH) == (6, 0, 6)
    draft_events = [item for item in get(client, "/api/history") if item["operation_id"] == operation_id]
    assert len(draft_events) == 1
    assert (draft_events[0]["event_type"], draft_events[0]["operation_status"]) == ("transfer_created", "draft")
    before = http_snapshot(client)
    assert post(client, "/api/transfers", transfer_payload())["id"] == operation_id
    assert http_snapshot(client) == before
    before = http_snapshot(client)
    assert client.post(f"/api/transfers/{operation_id}/receive").status_code == 409
    assert http_snapshot(client) == before
    post(client, f"/api/transfers/{operation_id}/dispatch")
    assert stock(client) == (7, 0, 7)
    assert stock(client, warehouse_id=SOUTH) == (6, 0, 6)
    assert get(client, "/api/stocks")["totals"]["transit_ff"] == 3
    before = http_snapshot(client)
    post(client, f"/api/transfers/{operation_id}/dispatch")
    assert http_snapshot(client) == before
    post(client, f"/api/transfers/{operation_id}/receive")
    assert stock(client) == (7, 0, 7)
    assert stock(client, warehouse_id=SOUTH) == (9, 0, 9)
    assert get(client, "/api/stocks")["totals"]["transit_ff"] == 0
    before = http_snapshot(client)
    assert post(client, f"/api/transfers/{operation_id}/dispatch")["status"] == "received"
    post(client, f"/api/transfers/{operation_id}/receive")
    assert http_snapshot(client) == before


def test_api_t05_fbo_receipt_only_finishes_transit(client):
    operation = post(client, "/api/fbo/shipments", dict(product_id=str(P102), warehouse_id=str(NORTH),
        qty=3, marketplace="Маркетплейс", idempotency_key="fbo"))
    assert stock(client, product_id=P102) == (2, 0, 2)
    assert get(client, "/api/stocks")["totals"]["transit_fbo"] == 3
    assert post(client, f"/api/fbo/{operation['id']}/receive")["status"] == "received"
    assert stock(client, product_id=P102) == (2, 0, 2)
    assert get(client, "/api/stocks")["totals"]["transit_fbo"] == 0
    before = http_snapshot(client)
    post(client, f"/api/fbo/{operation['id']}/receive")
    assert http_snapshot(client) == before


def test_api_t06_shortage_explains_local_stock_and_transfer_is_explicit(client):
    before = http_snapshot(client)
    response = client.post("/api/fbs/reservations", json=fbs_payload(qty=1, product_id=P103))
    assert response.status_code == 409
    error = response.json()
    assert isinstance(error["detail"], str) and error["detail"]
    assert error["available_here"] == 0 and error["requested"] == 1
    assert http_snapshot(client) == before
    operation = post(client, "/api/transfers", transfer_payload(qty=2, product_id=P103,
                                                               source=SOUTH, destination=NORTH))
    post(client, f"/api/transfers/{operation['id']}/dispatch")
    assert stock(client, P103, NORTH) == (0, 0, 0)
    assert stock(client, P103, SOUTH) == (2, 0, 2)
    post(client, f"/api/transfers/{operation['id']}/receive")
    assert stock(client, P103, NORTH) == (2, 0, 2)
    post(client, "/api/fbs/reservations", fbs_payload(qty=1, product_id=P103))
    assert stock(client, P103, NORTH) == (2, 1, 1)


@pytest.mark.parametrize("qty", [0, -1, 1.5, 1.0, True, "1", None, 2147483648])
def test_api_rejects_invalid_quantity_without_effect(client, qty):
    before = http_snapshot(client)
    response = client.post("/api/receipts", json=receipt_payload(qty=qty))
    assert response.status_code == 422
    error = response.json()
    assert isinstance(error["detail"], str)
    assert any(item["field"] == "body.qty" for item in error["errors"])
    assert http_snapshot(client) == before


@pytest.mark.parametrize("change", ["missing_key", "blank_key", "extra_field", "bad_uuid"])
def test_api_rejects_malformed_payload(client, change):
    payload = receipt_payload()
    if change == "missing_key":
        payload.pop("idempotency_key")
    elif change == "blank_key":
        payload["idempotency_key"] = "   "
    elif change == "extra_field":
        payload["unrecognized"] = "ignored fields would conceal mistakes"
    else:
        payload["warehouse_id"] = "not-a-uuid"
    before = http_snapshot(client)
    assert client.post("/api/receipts", json=payload).status_code == 422
    assert http_snapshot(client) == before


@pytest.mark.parametrize("missing", ["product", "warehouse", "operation"])
def test_api_unknown_ids_are_404(client, missing):
    before = http_snapshot(client)
    if missing == "operation":
        response = client.post(f"/api/fbs/{uuid4()}/ship")
    else:
        response = client.post("/api/receipts", json=receipt_payload(
            product_id=uuid4() if missing == "product" else P101,
            warehouse_id=uuid4() if missing == "warehouse" else NORTH))
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)
    assert http_snapshot(client) == before
    assert client.post("/api/fbs/not-a-uuid/ship").status_code == 422


def test_api_normalizes_customer_but_keeps_idempotency_key_opaque(client):
    payload = fbs_payload(qty=2, key="  normalized  ")
    payload["customer"] = "  Клиент  "
    first = post(client, "/api/fbs/reservations", payload)
    assert first["external_party"] == "Клиент"
    assert first["idempotency_key"] == "  normalized  "
    before = http_snapshot(client)
    payload.update(customer="Клиент")
    assert post(client, "/api/fbs/reservations", payload)["id"] == first["id"]
    assert http_snapshot(client) == before
    payload["qty"] = 3
    assert client.post("/api/fbs/reservations", json=payload).status_code == 409
    assert http_snapshot(client) == before
    different_key = post(client, "/api/fbs/reservations", dict(payload, qty=2, idempotency_key="normalized"))
    assert different_key["id"] != first["id"]
    assert different_key["idempotency_key"] == "normalized"
    assert stock(client) == (10, 4, 6)


def test_api_warehouse_create_rename_archive_preserves_inventory_and_old_history(client):
    warehouse = post(client, "/api/warehouses", {"name": "  Третий ФФ  "})
    warehouse_id = warehouse["id"]
    assert warehouse["name"] == "Третий ФФ" and warehouse["archived"] is False
    assert len(get(client, "/api/stocks")["balances"]) == 9
    operation = post(client, "/api/receipts", receipt_payload(warehouse_id=warehouse_id))
    response = client.patch(f"/api/warehouses/{warehouse_id}", json={"name": "Новое имя"})
    assert response.status_code == 200 and response.json()["id"] == warehouse_id
    response = client.delete(f"/api/warehouses/{warehouse_id}")
    assert response.status_code == 200 and response.json()["archived"] is True
    before = http_snapshot(client)
    assert client.delete(f"/api/warehouses/{warehouse_id}").json() == response.json()
    assert http_snapshot(client) == before
    listed = next(item for item in get(client, "/api/warehouses") if item["id"] == warehouse_id)
    assert listed["name"] == "Новое имя" and listed["archived"] is True
    assert stock(client, warehouse_id=warehouse_id) == (1, 0, 1)
    cell = next(cell for cell in get(client, "/api/stocks")["balances"]
                if cell["warehouse_id"] == warehouse_id and cell["product_id"] == str(P101))
    assert cell["archived"] is True
    event = next(item for item in get(client, "/api/history") if item["operation_id"] == operation["id"])
    assert event["to_name"] == "Третий ФФ" and event["to_warehouse_id"] == warehouse_id
    assert client.post("/api/receipts", json=receipt_payload(key="new", warehouse_id=warehouse_id)).status_code == 409
    assert http_snapshot(client) == before


def test_api_history_contains_complete_chronological_facts(client):
    operation = post(client, "/api/transfers", transfer_payload())
    post(client, f"/api/transfers/{operation['id']}/dispatch")
    post(client, f"/api/transfers/{operation['id']}/receive")
    history = get(client, "/api/history")
    times = [datetime.fromisoformat(event["created_at"]) for event in history]
    assert times == sorted(times, reverse=True)
    rows = [event for event in history if event["operation_id"] == operation["id"]]
    assert len(rows) == 3
    assert {event["event_type"]: event["operation_status"] for event in rows} == {
        "transfer_created": "draft", "transfer_dispatched": "in_transit", "transfer_received": "received"}
    for event in rows:
        assert event["sku"] == "SKU-101" and event["qty"] == 3
        assert event["from_name"] == "Север" and event["to_name"] == "Юг"
        assert event["from_warehouse_id"] == str(NORTH) and event["to_warehouse_id"] == str(SOUTH)
        assert datetime.fromisoformat(event["created_at"]).utcoffset().total_seconds() == 0
    current = next(item for item in get(client, "/api/operations") if item["id"] == operation["id"])
    assert current["status"] == "received"


def test_api_t10_repeated_receipt_never_doubles_stock(client):
    payload = receipt_payload(qty=2, key="repeat-receipt")
    first = post(client, "/api/receipts", payload)
    assert stock(client) == (12, 0, 12)
    assert get(client, "/api/stocks")["totals"]["on_hand"] == 27
    before = http_snapshot(client)
    assert post(client, "/api/receipts", payload)["id"] == first["id"]
    assert http_snapshot(client) == before
    assert client.post("/api/receipts", json=dict(payload, qty=3)).status_code == 409
    assert http_snapshot(client) == before
    assert len([event for event in get(client, "/api/history")
                if event["operation_id"] == first["id"]]) == 1


@pytest.mark.parametrize("path,payload_factory", CREATE_ROUTES)
def test_api_each_creation_route_replays_without_duplicate_effect(client, path, payload_factory):
    payload = payload_factory(qty=2, key="create-replay")
    operation = post(client, path, payload)
    before = http_snapshot(client)
    assert post(client, path, payload)["id"] == operation["id"]
    assert http_snapshot(client) == before
    conflict = client.post(path, json=dict(payload, qty=3))
    assert conflict.status_code == 409
    assert isinstance(conflict.json()["detail"], str)
    assert http_snapshot(client) == before


@pytest.mark.parametrize("path,payload_factory", CREATE_ROUTES)
@pytest.mark.parametrize("invalid", ["boolean_quantity", "extra_field"])
def test_api_every_command_uses_strict_request_schema(client, path, payload_factory, invalid):
    payload = payload_factory()
    if invalid == "boolean_quantity":
        payload["qty"] = True
    else:
        payload["unexpected"] = "must-not-be-ignored"
    before = http_snapshot(client)
    response = client.post(path, json=payload)
    assert response.status_code == 422
    assert response.json()["errors"]
    assert http_snapshot(client) == before


def test_api_same_warehouse_transfer_is_422_without_history_or_balance_change(client):
    before = http_snapshot(client)
    response = client.post("/api/transfers", json=transfer_payload(destination=NORTH))
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert http_snapshot(client) == before


@pytest.mark.parametrize("body", ["{", "[]", "null", '"text"', ""])
def test_api_malformed_or_nonobject_json_returns_safe_validation_error(client, body):
    before = http_snapshot(client)
    response = client.post("/api/receipts", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    error = response.json()
    assert isinstance(error["detail"], str) and error["errors"]
    assert all(set(item) == {"field", "message"} for item in error["errors"])
    assert http_snapshot(client) == before


@pytest.mark.parametrize("path,payload_factory,field", [
    ("/api/receipts", receipt_payload, "idempotency_key"),
    ("/api/transfers", transfer_payload, "idempotency_key"),
    ("/api/fbs/reservations", fbs_payload, "customer"),
    ("/api/fbo/shipments", fbo_payload, "marketplace"),
    ("/api/warehouses", lambda: {"name": "Склад"}, "name"),
])
@pytest.mark.parametrize("unsafe_text", ["bad\x00value", "bad\ud800value"])
def test_api_rejects_postgresql_incompatible_text_without_echoing_input(
    client, path, payload_factory, field, unsafe_text,
):
    payload = payload_factory()
    payload[field] = unsafe_text
    before = http_snapshot(client)
    response = client.post(path, content=json.dumps(payload, ensure_ascii=True),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    error = response.json()
    assert isinstance(error["detail"], str) and error["errors"]
    for item in error["errors"]:
        assert set(item) == {"field", "message"}
        item["field"].encode("utf-8")
        item["message"].encode("utf-8")
    assert unsafe_text not in json.dumps(error, ensure_ascii=False)
    assert http_snapshot(client) == before


def test_api_surrogate_in_unexpected_field_name_still_has_safe_422_response(client):
    payload = receipt_payload()
    payload["invalid\ud800field"] = "raw-input-must-not-be-echoed"
    before = http_snapshot(client)
    response = client.post("/api/receipts", content=json.dumps(payload, ensure_ascii=True),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    error = response.json()
    assert error["errors"]
    assert "raw-input-must-not-be-echoed" not in response.text
    for item in error["errors"]:
        assert set(item) == {"field", "message"}
        item["field"].encode("utf-8")
        item["message"].encode("utf-8")
    assert http_snapshot(client) == before


@pytest.mark.parametrize("route", TRANSITION_ROUTES)
def test_api_transition_uuid_validation_missing_id_and_wrong_kind(client, route):
    before = http_snapshot(client)
    assert client.post(route.format(id="not-a-uuid")).status_code == 422
    assert client.post(route.format(id=uuid4())).status_code == 404
    receipt_id = get(client, "/api/operations")[0]["id"]
    response = client.post(route.format(id=receipt_id))
    assert response.status_code == 409
    assert isinstance(response.json()["detail"], str)
    assert http_snapshot(client) == before


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_api_warehouse_path_errors_do_not_modify_catalog(client, method):
    before = http_snapshot(client)
    request = getattr(client, method)
    kwargs = {"json": {"name": "Новое имя"}} if method == "patch" else {}
    assert request(f"/api/warehouses/{uuid4()}", **kwargs).status_code == 404
    assert request("/api/warehouses/invalid", **kwargs).status_code == 422
    assert http_snapshot(client) == before


@pytest.mark.parametrize("kind", ["transfer", "fbo"])
def test_api_other_shipments_respect_local_fbs_reserve(client, kind):
    post(client, "/api/fbs/reservations", fbs_payload(qty=8))
    if kind == "transfer":
        operation = post(client, "/api/transfers", transfer_payload(qty=3))
        path, payload = f"/api/transfers/{operation['id']}/dispatch", None
    else:
        path, payload = "/api/fbo/shipments", fbo_payload(qty=3)
    before = http_snapshot(client)
    response = client.post(path, json=payload) if payload is not None else client.post(path)
    assert response.status_code == 409
    assert response.json()["available_here"] == 2
    assert response.json()["requested"] == 3
    assert http_snapshot(client) == before


def test_api_archive_blocks_new_work_but_allows_existing_and_outgoing_transfers(client):
    incoming = post(client, "/api/transfers", transfer_payload(qty=2, key="old-incoming"))
    reservation = post(client, "/api/fbs/reservations", fbs_payload(qty=2, key="old-fbs", warehouse_id=SOUTH))
    shipment = post(client, "/api/fbo/shipments", fbo_payload(qty=1, key="old-fbo", warehouse_id=SOUTH))
    assert client.delete(f"/api/warehouses/{SOUTH}").status_code == 200
    before = http_snapshot(client)
    for path, payload in [
        ("/api/receipts", receipt_payload(key="new-receipt", warehouse_id=SOUTH)),
        ("/api/fbs/reservations", fbs_payload(qty=1, key="new-fbs", warehouse_id=SOUTH)),
        ("/api/fbo/shipments", fbo_payload(qty=1, key="new-fbo", warehouse_id=SOUTH)),
        ("/api/transfers", transfer_payload(qty=1, key="new-incoming")),
    ]:
        assert client.post(path, json=payload).status_code == 409
        assert http_snapshot(client) == before
    post(client, f"/api/transfers/{incoming['id']}/dispatch")
    post(client, f"/api/transfers/{incoming['id']}/receive")
    post(client, f"/api/fbs/{reservation['id']}/ship")
    post(client, f"/api/fbo/{shipment['id']}/receive")
    assert stock(client, warehouse_id=SOUTH) == (5, 0, 5)
    outgoing = post(client, "/api/transfers", transfer_payload(
        qty=2, key="evacuate", product_id=P103, source=SOUTH, destination=NORTH,
    ))
    post(client, f"/api/transfers/{outgoing['id']}/dispatch")
    post(client, f"/api/transfers/{outgoing['id']}/receive")
    assert stock(client, P103, SOUTH) == stock(client, P103, NORTH) == (2, 0, 2)


@pytest.mark.parametrize("failure", ["domain", "database"])
def test_api_receipt_failure_rolls_back_before_error_response(client, monkeypatch, failure):
    before = http_snapshot(client)

    def fail_after_balance_and_operation(session, *args, **kwargs):
        session.flush()
        if failure == "domain":
            raise DomainError("Приход отклонён при проверке")
        raise OperationalError("private-statement-marker", None, RuntimeError("private-driver-marker"))

    monkeypatch.setattr(stock_services, "add_event", fail_after_balance_and_operation)
    response = client.post("/api/receipts", json=receipt_payload(qty=2, key="rollback-http"))
    assert response.status_code == (409 if failure == "domain" else 503)
    assert isinstance(response.json()["detail"], str)
    assert "private-statement-marker" not in response.text and "private-driver-marker" not in response.text
    assert http_snapshot(client) == before


def test_api_warehouse_failure_rolls_back_catalog_change(client, monkeypatch):
    from app.api import warehouses
    before = http_snapshot(client)

    def reject_projection(warehouse):
        raise DomainError("Изменение склада отклонено")

    monkeypatch.setattr(warehouses, "warehouse_view", reject_projection)
    response = client.post("/api/warehouses", json={"name": "Не должен сохраниться"})
    assert response.status_code == 409
    assert http_snapshot(client) == before


def test_concurrent_http_reservations_have_independent_transactions(client, db, monkeypatch):
    original = operations.reserve_fbs
    barrier = Barrier(2, timeout=10)
    backend_pids = []

    def synchronized_reserve(session, **payload):
        backend_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
        barrier.wait()
        return original(session, **payload)

    monkeypatch.setattr(operations, "reserve_fbs", synchronized_reserve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, "/api/fbs/reservations", json=fbs_payload(
            qty=7, key=f"http-race-{index}",
        )) for index in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(set(backend_pids)) == 2
    assert stock(client) == (10, 7, 3)
    winner = next(response.json()["id"] for response in responses if response.status_code == 200)
    assert len([item for item in get(client, "/api/history") if item["operation_id"] == winner]) == 1


@pytest.mark.parametrize("action", ["ship", "deliver"])
def test_concurrent_http_confirmation_has_one_effect(client, monkeypatch, action):
    operation = post(client, "/api/fbs/reservations", fbs_payload(qty=2))
    if action == "deliver":
        post(client, f"/api/fbs/{operation['id']}/ship")
    original = getattr(operations, f"{action}_fbs")
    barrier = Barrier(2, timeout=10)
    backend_pids = []

    def synchronized_ship(session, operation_id):
        backend_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
        barrier.wait()
        return original(session, operation_id)

    monkeypatch.setattr(operations, f"{action}_fbs", synchronized_ship)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, f"/api/fbs/{operation['id']}/{action}") for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert [response.status_code for response in responses] == [200, 200]
    assert len(set(backend_pids)) == 2
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["status"] == ("delivered" if action == "deliver" else "shipped")
    assert {response.json()["id"] for response in responses} == {operation["id"]}
    assert stock(client) == (8, 0, 8)
    assert get(client, "/api/stocks")["totals"]["transit_fbs"] == (0 if action == "deliver" else 2)
    assert len([item for item in get(client, "/api/history") if item["operation_id"] == operation["id"]]) == (
        3 if action == "deliver" else 2
    )
