"""The Stage D API is discoverable and usable with locally served Swagger assets."""

from html.parser import HTMLParser

from fastapi.testclient import TestClient
import pytest

from app.main import app


@pytest.fixture
def docs_client():
    # Documentation and static assets do not require a database connection.
    with TestClient(app) as client:
        yield client


REQUIRED_ROUTES = {
    "/api/stocks": {"get"},
    "/api/warehouses": {"get", "post"},
    "/api/warehouses/{warehouse_id}": {"patch", "delete"},
    "/api/receipts": {"post"},
    "/api/transfers": {"post"},
    "/api/transfers/{operation_id}/dispatch": {"post"},
    "/api/transfers/{operation_id}/receive": {"post"},
    "/api/fbs/reservations": {"post"},
    "/api/fbs/{operation_id}/ship": {"post"},
    "/api/fbs/{operation_id}/deliver": {"post"},
    "/api/fbs/{operation_id}/cancel": {"post"},
    "/api/fbo/shipments": {"post"},
    "/api/fbo/{operation_id}/receive": {"post"},
    "/api/operations": {"get"},
    "/api/history": {"get"},
}


def test_openapi_documents_all_required_routes_successes_and_error_shapes(docs_client):
    response = docs_client.get("/openapi.json")
    assert response.status_code == 200
    specification = response.json()
    assert specification["info"]["title"] == "CheckStock"
    for path, methods in REQUIRED_ROUTES.items():
        assert methods <= set(specification["paths"][path])
        for method in methods:
            responses = specification["paths"][path][method]["responses"]
            assert responses["200"]["content"]["application/json"]["schema"]
            for status in ("404", "409", "422", "503", "500"):
                assert responses[status]["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ErrorView",
                }
    error_schema = specification["components"]["schemas"]["ErrorView"]
    assert error_schema["properties"]["detail"]["type"] == "string"
    assert "detail" in error_schema["required"]


def test_openapi_exposes_request_constraints_and_concrete_response_models(docs_client):
    schemas = docs_client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("ReceiptInput", "TransferInput", "FbsInput", "FboInput"):
        request = schemas[name]
        assert request["additionalProperties"] is False
        assert {"product_id", "qty", "idempotency_key"} <= set(request["required"])
        assert request["properties"]["product_id"]["format"] == "uuid"
        assert request["properties"]["qty"]["type"] == "integer"
        assert request["properties"]["qty"]["exclusiveMinimum"] == 0
        assert request["properties"]["qty"]["maximum"] == 2147483647
        assert request["properties"]["idempotency_key"]["minLength"] == 1
        assert request["properties"]["idempotency_key"]["maxLength"] == 200
    assert schemas["WarehouseInput"]["additionalProperties"] is False
    assert {"InventoryView", "OperationView", "StockEventView", "WarehouseView"} <= set(schemas)
    assert "delivered" in schemas["OperationView"]["properties"]["status"]["enum"]


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and "src" in attributes:
            self.urls.append(attributes["src"])
        if tag == "link" and "href" in attributes:
            self.urls.append(attributes["href"])


def test_swagger_html_loads_only_local_assets_and_disables_external_validator(docs_client):
    response = docs_client.get("/docs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    parser = AssetReferences()
    parser.feed(response.text)
    assert set(parser.urls) == {
        "/static/vendor/swagger-ui-bundle.js",
        "/static/vendor/swagger-ui.css",
        "/static/vendor/favicon-32x32.png",
    }
    assert all(url.startswith("/static/vendor/") for url in parser.urls)
    assert '"validatorUrl": null' in response.text
    assert "/openapi.json" in response.text
    assert "SwaggerUIBundle(" in response.text


@pytest.mark.parametrize("filename,content_types", [
    ("swagger-ui-bundle.js", {"text/javascript", "application/javascript"}),
    ("swagger-ui.css", {"text/css"}),
    ("favicon-32x32.png", {"image/png"}),
])
def test_swagger_runtime_assets_are_served_from_the_application(docs_client, filename, content_types):
    response = docs_client.get(f"/static/vendor/{filename}")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] in content_types
    assert response.content
    if filename.endswith(".png"):
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    elif filename.endswith(".js"):
        assert "SwaggerUIBundle" in response.text


@pytest.mark.parametrize("filename", ["LICENSE", "NOTICE", "swagger-ui-bundle.js.LICENSE.txt"])
def test_vendored_swagger_distribution_preserves_license_notices(docs_client, filename):
    response = docs_client.get(f"/static/vendor/{filename}")
    assert response.status_code == 200
    assert response.content.strip()
