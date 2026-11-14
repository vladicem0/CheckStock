"""Synchronous HTTP API, local OpenAPI documentation and PostgreSQL readiness."""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import inventory, operations, warehouses
from app.db import get_session
from app.schemas import ErrorView
from app.services.stock import DomainError

app = FastAPI(
    title="CheckStock",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)

app.mount(
    "/static/vendor",
    StaticFiles(directory=Path(__file__).resolve().parent / "static" / "vendor"),
    name="swagger-assets",
)

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


@app.exception_handler(SQLAlchemyError)
async def database_error(_request: Request, _error: SQLAlchemyError) -> JSONResponse:
    # Transaction contexts have rolled back before errors reach this boundary.
    return JSONResponse(status_code=503, content={"detail": "База данных временно недоступна"})


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


@app.get("/health", responses={503: {"model": ErrorView}}, tags=["Готовность"])
def health(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
    """Report readiness only when a database query succeeds."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="База данных недоступна") from error
    return {"status": "ok"}
