"""Synchronous HTTP API, local OpenAPI documentation and PostgreSQL readiness."""

import logging
from pathlib import Path
from traceback import extract_tb
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy.orm import Session

from app.api import inventory, operations, warehouses
from app.db import get_session
from app.schemas import ErrorView
from app.services.stock import DomainError

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CheckStock",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")

ERROR_RESPONSES = {
    404: {"model": ErrorView, "description": "Товар, склад или операция не найдены"},
    409: {"model": ErrorView, "description": "Операция противоречит текущему состоянию учёта"},
    422: {"model": ErrorView, "description": "Некорректные параметры запроса"},
    503: {"model": ErrorView, "description": "База данных временно недоступна"},
    500: {"model": ErrorView, "description": "Внутренняя ошибка сервера"},
}
for router in (warehouses.router, operations.router, inventory.router):
    app.include_router(router, responses=ERROR_RESPONSES)


@app.exception_handler(DomainError)
async def domain_error(_request: Request, error: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.message, **error.details},
    )


_VALIDATION_MESSAGES = {
    "missing": "Обязательное поле",
    "extra_forbidden": "Неизвестное поле",
    "int_type": "Ожидается целое число",
    "int_parsing": "Ожидается целое число",
    "greater_than": "Количество должно быть больше нуля",
    "less_than_equal": "Количество превышает допустимый предел",
    "string_type": "Ожидается строка",
    "string_too_short": "Строка не должна быть пустой",
    "string_too_long": "Строка слишком длинная",
    "uuid_parsing": "Некорректный UUID",
    "uuid_type": "Ожидается UUID",
    "json_invalid": "Некорректный JSON",
    "model_attributes_type": "Ожидается объект JSON",
    "model_type": "Ожидается объект JSON",
}


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    # Validation input/context can contain non-JSON values or private payload data.
    # Even a field name supplied as an extra JSON key can contain a surrogate.
    errors = [
        {
            "field": ".".join(str(part) for part in item["loc"])
            .encode("utf-8", errors="replace").decode("utf-8"),
            "message": _VALIDATION_MESSAGES.get(item["type"], "Некорректное значение"),
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": "Проверьте параметры запроса", "errors": errors},
    )


def _log_database_error(error: SQLAlchemyError, status_code: int) -> None:
    # Keep diagnostic locations without SQL, bound parameters, driver messages,
    # request bodies or frame locals, which may contain private data.
    locations = " -> ".join(
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in extract_tb(error.__traceback__)
    )
    logger.error(
        "Database request failed: type=%s status=%s traceback=%s",
        type(error).__name__, status_code, locations,
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(_request: Request, error: SQLAlchemyError) -> JSONResponse:
    # Transaction contexts have rolled back before errors reach this boundary.
    unavailable = isinstance(error, (OperationalError, InterfaceError, TimeoutError)) or (
        isinstance(error, DBAPIError) and error.connection_invalidated
    )
    status_code = 503 if unavailable else 500
    _log_database_error(error, status_code)
    detail = "База данных временно недоступна" if unavailable else "Внутренняя ошибка сервера"
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, _error: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


@app.get("/docs", include_in_schema=False, response_class=HTMLResponse)
def api_documentation() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="CheckStock — API",
        swagger_js_url="/static/vendor/swagger-ui-bundle.js",
        swagger_css_url="/static/vendor/swagger-ui.css",
        swagger_favicon_url="/static/vendor/favicon-32x32.png",
        swagger_ui_parameters={"validatorUrl": None},
    )


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Render a local shell; inventory reads and commands use the existing API."""
    return templates.TemplateResponse(
        request=request, name="index.html", headers={"Cache-Control": "no-store"},
    )


@app.get("/health", responses={503: {"model": ErrorView}}, tags=["Готовность"])
def health(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
    """Report readiness only when a database query succeeds."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        _log_database_error(error, 503)
        raise HTTPException(status_code=503, detail="База данных недоступна") from error
    return {"status": "ok"}
