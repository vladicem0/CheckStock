"""HTTP smoke checks for the browser shell; interactive flows are checked in a browser."""
from html.parser import HTMLParser
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from app.db import get_session
from app.main import app


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.resources = []
        self.forms = {}
        self.controls = []
        self.labels = set()
        self.ids = []
        self.submits = set()
        self.html_attributes = {}
        self.current_form = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "html":
            self.html_attributes = attrs
        if tag == "script" and attrs.get("src"):
            self.resources.append(attrs["src"])
        if tag == "link" and set(attrs.get("rel", "").split()) & {"stylesheet", "icon"}:
            self.resources.append(attrs["href"])
        if tag == "form":
            self.current_form = attrs.get("id")
            self.forms[self.current_form] = attrs
        if tag == "label" and attrs.get("for"):
            self.labels.add(attrs["for"])
        if tag in {"input", "select", "textarea"} and attrs.get("type") != "hidden":
            self.controls.append((self.current_form, tag, attrs))
        if tag == "button" and attrs.get("type", "submit") == "submit" and self.current_form:
            self.submits.add(self.current_form)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None


@pytest.fixture
def web_client(db):
    def isolated_session():
        with db() as session:
            yield session

    app.dependency_overrides[get_session] = isolated_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


def assert_local_resources_load(client, page):
    assert page.resources, "Page must serve its CSS and JavaScript resources"
    extensions = set()
    for url in page.resources:
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            base = urlsplit(str(client.base_url))
            assert (parsed.scheme, parsed.netloc) == (base.scheme, base.netloc), f"External asset dependency: {url}"
        assert parsed.path.startswith("/static/"), url
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.content, url
        extension = parsed.path.rsplit(".", 1)[-1]
        extensions.add(extension)
        content_type = response.headers["content-type"].split(";", 1)[0]
        if extension == "css":
            assert content_type == "text/css"
        elif extension == "js":
            assert content_type in {"text/javascript", "application/javascript"}
        elif extension == "png":
            assert content_type == "image/png"
    assert {"js", "css"} <= extensions


def test_web_shell_is_html_with_available_local_assets(web_client):
    response = web_client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = Page(response.text)
    assert page.html_attributes.get("lang") == "ru"
    assert len(page.ids) == len(set(page.ids)), "Duplicate IDs break labels and DOM actions"
    assert_local_resources_load(web_client, page)
    assert web_client.get("/api/stocks").json()["totals"]["on_hand"] == 25


def test_operation_forms_have_required_accessible_inputs_and_integer_quantities(web_client):
    page = Page(web_client.get("/").text)
    required = {
        "receipt-form": ("/api/receipts", {"product_id", "warehouse_id", "qty"}),
        "transfer-form": ("/api/transfers", {"product_id", "source_warehouse_id", "destination_warehouse_id", "qty"}),
        "fbs-form": ("/api/fbs/reservations", {"product_id", "warehouse_id", "qty", "customer"}),
        "fbo-form": ("/api/fbo/shipments", {"product_id", "warehouse_id", "qty", "marketplace"}),
        "warehouse-form": ("/api/warehouses", {"name"}),
    }
    for form_id, (action, names) in required.items():
        assert form_id in page.forms
        assert page.forms[form_id]["method"].lower() == "post"
        assert page.forms[form_id]["action"] == action
        assert form_id in page.submits
        controls = {attrs["name"]: attrs for owner, tag, attrs in page.controls if owner == form_id}
        assert names <= set(controls)
        for name in names:
            attrs = controls[name]
            assert "required" in attrs
            assert attrs.get("id") in page.labels or attrs.get("aria-label")
        if "qty" in names:
            attrs = controls["qty"]
            assert attrs["type"] == "number" and attrs["min"] == "1" and attrs["step"] == "1"
            assert attrs["max"] == "2147483647"


def test_swagger_is_self_hosted_and_its_assets_are_available(web_client):
    response = web_client.get("/docs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_local_resources_load(web_client, Page(response.text))
    openapi = web_client.get("/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["info"]["title"] == "CheckStock"


def test_panel_and_assets_render_when_database_is_unavailable():
    def unavailable_session():
        raise RuntimeError("database dependency must not be used for the shell")
        yield

    app.dependency_overrides[get_session] = unavailable_session
    try:
        with TestClient(app) as client:
            response = client.get("/")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert_local_resources_load(client, Page(response.text))
            assert "global-error" in response.text
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_panel_uses_existing_api_without_exposing_templates_or_source(web_client):
    page = Page(web_client.get("/").text)
    assert {"overview", "transit", "operations", "warehouses", "history"} <= set(page.ids)
    assert "/" not in web_client.get("/openapi.json").json()["paths"]
    for path in ("/templates/base.html", "/templates/index.html", "/static/main.py"):
        assert web_client.get(path).status_code == 404
