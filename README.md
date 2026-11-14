# CheckStock

Локальный прототип складского учёта по контракту [CHECKSTOCK_CODEX_PLAN.md](CHECKSTOCK_CODEX_PLAN.md). В этапе B подготовлены PostgreSQL, схема и ограничения БД, миграция Alembic, сервис прихода, явное создание демонстрационных данных и проверка готовности FastAPI. Операции перемещений/FBS/FBO, HTTP API учёта и пользовательский интерфейс относятся к следующим этапам.

## Запуск

Нужны Docker Engine с Linux-контейнерами и Docker Compose v2+. На Windows запустите Docker Desktop и дождитесь готовности Engine. Python и PostgreSQL на хосте для приложения не требуются. Первой сборке нужен доступ к образам и Python-зависимостям.

В корне клонированного проекта проверьте Docker:

```text
docker version
docker compose version
```

В выводе `docker version` должны присутствовать Client и Server. Создайте локальный файл настроек из примера.

PowerShell:

```powershell
Copy-Item .env.example .env
```

Linux/macOS:

```sh
cp .env.example .env
```

В `.env` замените `POSTGRES_PASSWORD` своим случайным паролем из ASCII-букв и цифр: пароль включается в URL подключения. При необходимости измените `APP_PORT` (по умолчанию `8000`). `.env` исключён из Git и контекста сборки. Для уже созданного тома изменение этой переменной само по себе не меняет пароль PostgreSQL.

```text
docker compose config -q
docker compose up --build -d
docker compose up -d --wait
docker compose ps
```

Оба сервиса должны стать `healthy`. Приложение выполняет `alembic upgrade head`, затем запускает Uvicorn от непривилегированного пользователя. [Проверка готовности](http://localhost:8000/health) возвращает `{"status":"ok"}` после успешного запроса к PostgreSQL, а при недоступности БД — HTTP 503. HTTP доступен только на `127.0.0.1`, порт PostgreSQL наружу не опубликован. На этапе B маршруты `/`, `/docs` и API учёта ещё не предоставляются.

## Демонстрационные приходы

На чистой базе товары и склады отсутствуют. Миграции не создают демонстрационные данные, приложение не загружает их автоматически. Выполните seed явно, затем повторите для проверки идемпотентности:

```text
docker compose exec -T app python -m app.seed
docker compose exec -T app python -m app.seed
```

| SKU | Название | Север | Юг |
|---|---|---:|---:|
| SKU-101 | Органайзер | 10 | 6 |
| SKU-102 | Контейнер | 5 | 0 |
| SKU-103 | Корзина | 0 | 4 |

Итого: три товара, два склада, шесть ячеек остатков, физически **25 шт.**, резерв и транзиты — **0**. Четыре положительных количества оформляются как операции прихода с четырьмя событиями; нули не создают фиктивных приходов. Балансы не присваиваются напрямую. Seed использует стабильные UUID и ключи идемпотентности: повтор не удваивает количество, не сбрасывает изменённые остатки и не переименовывает справочники обратно.

Проверить количества и журнал можно через PostgreSQL:

```text
docker compose exec -T db psql -U checkstock -d checkstock -c "SELECT p.sku, w.name, COALESCE(b.on_hand, 0) AS on_hand, COALESCE(b.reserved_fbs, 0) AS reserved_fbs FROM products p CROSS JOIN warehouses w LEFT JOIN stock_balances b ON b.product_id = p.id AND b.warehouse_id = w.id ORDER BY p.sku, w.name;"
docker compose exec -T db psql -U checkstock -d checkstock -c "SELECT sum(on_hand) AS physical, sum(reserved_fbs) AS reserved FROM stock_balances; SELECT count(*) AS receipts FROM operations WHERE kind = 'receipt'; SELECT count(*) AS events FROM stock_events;"
```

## Проверки

```text
docker compose exec -T app pytest -q
docker compose exec -T app alembic check
```

Тесты приложения используют настоящий PostgreSQL. Каждый тест создаёт отдельную схему `test_checkstock_<uuid>` и применяет к ней миграцию. Очистка удаляет только собственную случайную схему; рабочая `public` и демонстрационные остатки сохраняются. Проверяются начальные данные, повторные приходы, атомарность и ограничения БД. Эти проверки не означают готовность сценариев следующих этапов.

Тесты контроллера независимого ревью запускаются отдельно на хосте с Python и Git; они исключены из образа приложения и не подменяют PostgreSQL-тесты:

```text
python -m unittest discover -s tests/review -v
```

Для проверки сохранения данных выполните запрос суммы до и после перезапуска PostgreSQL:

```text
docker compose exec -T db psql -U checkstock -d checkstock -c "SELECT sum(on_hand) AS physical FROM stock_balances;"
docker compose restart db
docker compose up -d --wait
docker compose exec -T db psql -U checkstock -d checkstock -c "SELECT sum(on_hand) AS physical FROM stock_balances;"
```

Без дополнительных приходов обе суммы равны 25. PostgreSQL хранится в именованном томе `checkstock_postgres_data`. Обычная остановка сохраняет данные:

```text
docker compose down
```

Не удаляйте рабочий том для подготовки тестов. Для отдельной чистой проверки задайте новое имя проекта через `docker compose -p checkstock_stage_b_check ...` во всех командах и свободный `APP_PORT` в отдельном файле настроек; такой проект получит собственный том. Команда `docker compose config -q` проверяет конфигурацию без вывода пароля.

## Архитектура и границы

Синхронный FastAPI, SQLAlchemy 2 + psycopg, PostgreSQL 17 и Alembic. PostgreSQL — единственный источник истины; `create_all()` в пути запуска не используется. Зависимости зафиксированы в `pyproject.toml`.

- `app/config.py`, `app/db.py` — конфигурация и сессии PostgreSQL.
- `app/models.py`, `alembic/` — таблицы, индексы и миграция.
- `app/services/` — сервис прихода и необходимые транзакционные примитивы.
- `app/seed.py` — явные демонстрационные приходы через сервис.
- `app/main.py` — проверка готовности приложения и БД.
- `tests/` — проверки приложения; `tests/review/` — отдельный контроллер ревью.

Приход объединяет проверку, изменение баланса, операцию и событие в одной транзакции. Сервис не выполняет отдельный commit. Уникальный ключ и отпечаток запроса защищают от повторного прихода; блокировки сериализуют запись. Ограничения БД запрещают отрицательные остатки, резерв выше физического, неверные статусы/назначения и дубли событий. Журнал защищён от UPDATE/DELETE/TRUNCATE. Метки времени хранятся в `timestamptz`.

Доменные договорённости всех этапов зафиксированы в [DECISIONS](docs/DECISIONS.md), ожидаемые результаты — в [ACCEPTANCE](docs/ACCEPTANCE.md). Создание черновика перемещения будет фиксировать `transfer_created` без изменения остатков и транзита; реализация жизненного цикла относится к этапу C.

Это локальный тестовый прототип без аутентификации. Не публикуйте его в интернете. Здесь нет интеграций с маркетплейсами, частичных приёмок, возвратов, многомагазинности или подтверждения доставки FBS. Следующие этапы добавят бизнес-сценарии, API, интерфейс и материалы окончательной сдачи.
