Проект для обработки звонков (КЦ / 911): сканирование аудио, транскрибация Whisper, хранение в PostgreSQL, фоновые задачи Celery (транскрибация, классификация, саммаризация, каталог), FastAPI + UI с ролевым доступом.

## 1) Подготовка окружения (локально)

1. Виртуальное окружение Python **3.11**.
2. Установка зависимостей: `pip install -r requirements.txt`
3. Скопируйте `.env.example` в `.env` и заполните значения.

Если pip пишет про `\x00` в первой строке `requirements.txt` или «Invalid requirement» с пробелами между буквами — файл случайно сохранён как **UTF-16**. Нужна кодировка **UTF-8** (в VS Code / PyCharm: *Reopen with Encoding* / *Save with Encoding* → UTF-8), либо заново взять файл из репозитория.

## 2) Параметры `.env`

**Обязательные (минимум для API):**

- `DATABASE_URL`
- `API_KEY_911`, `API_KEY_KC`, `API_KEY_ADMIN`
- `SESSION_SECRET` (сессия UI)

**Сервис:**

- `APP_HOST`, `APP_PORT`

**UI и вход:**

- **LDAP** (основной вход): `LDAP_URL`, `LDAP_BASE_DN`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_FILTER`, группы `LDAP_GROUP_DN_*`
- Опционально **суперпользователь** (полный ADMIN, без LDAP): `UI_SUPERUSER_LOGIN` / `UI_SUPERUSER_PASSWORD` (или `SUPERUSER_*`)

**Celery / Redis** (если воркеры не только в Docker):

- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` (например `redis://localhost:6379/0`)

**Flower** (монитор очередей; в проде задайте пароль):

- `FLOWER_BASIC_AUTH=user:password`
- `FLOWER_PORT` (по умолчанию в compose — 5555)

**Модели и RAG:** см. `.env.example` (`WHISPER_*`, `GEMMA_MODEL_PATH`, эмбеддинги, `QDRANT_*`).

## 3) Запуск FastAPI без Docker

```bash
uvicorn api_service.main:app --host 0.0.0.0 --port 5000
```

- Swagger: `http://<host>:5000/docs`
- UI: `http://<host>:5000/` (вход), `http://<host>:5000/calls`, `/classified-calls`, `/users`, `/pipeline-runs` (ADMIN), `/catalog` (ADMIN)
- Проверка готовности: `GET /healthz`

## 4) Docker Compose

Файл `docker-compose.yml` поднимает связку для прода/стенда:

| Сервис | Назначение |
|--------|------------|
| `redis` | Брокер и backend результатов Celery |
| `web` | FastAPI (`uvicorn`), порт **5000** |
| `worker-transcribe` | Воркер очереди **`q.transcribe`** (тяжёлая транскрибация), `concurrency=1` |
| `worker-light` | Воркер очередей **`q.classify`**, **`q.catalog`**, **`q.summarize`** |
| `flower` | Веб‑монитор Celery, порт **5555** (см. ниже) |

Запуск из корня репозитория (нужны `.env` и доступная схема БД):

```bash
docker compose up -d --build
```

Переменные **`CELERY_BROKER_URL`** / **`CELERY_RESULT_BACKEND`** для `web` и воркеров в compose заданы на `redis://redis:6379/0`. Путь к моделям и `DATABASE_URL` должны указывать на те ресурсы, которые видит контейнер (при необходимости добавьте **volumes** в `docker-compose.yml` для каталогов с Whisper/Gemma и сетевой доступ к Postgres/Qdrant).

Образ собирается из `Dockerfile` (Python 3.11, ffmpeg, зависимости из `requirements.txt`).

## 5) Очереди Celery

Конфигурация приложения: `app/celery_app.py` (автообнаружение задач в пакете **`jobs`**).

| Очередь | Задачи (по маршрутизации) |
|---------|---------------------------|
| `q.transcribe` | `jobs.transcribe_*` |
| `q.classify` | `jobs.classify_*` |
| `q.catalog` | `jobs.catalog_*` (например генерация синонимов после сохранения справочника) |
| `q.summarize` | `jobs.summarize_*` |

Отдельные контейнеры в compose разделяют тяжёлую транскрибацию и «лёгкие» задачи. При необходимости добавьте ещё воркеры с тем же образом и другим `-Q`.

Ручная постановка в очередь (см. также `scripts/enqueue.py`): вызов `.delay()` на задачах из `jobs/` при работающем брокере и воркерах.

## 6) Flower: как пользоваться

1. После `docker compose up` откройте **`http://<хост>:5555/`** (порт см. `FLOWER_PORT`, по умолчанию 5555).
2. Если задан **`FLOWER_BASIC_AUTH`** в `.env`, браузер запросит логин и пароль.
3. Полезные разделы:
   - **Workers** — какие воркеры онлайн, какие очереди слушают, нагрузка;
   - **Tasks** — активные, успешные, сбойные задачи;
   - **Monitor** — поток событий в реальном времени.
4. **Безопасность:** не выставляйте Flower в публичную сеть без пароля и по возможности без VPN/firewall; это полный доступ к метаданным очередей и управлению воркерами.

Flower использует тот же Redis, что и воркеры (`CELERY_BROKER_URL` в сервисе `flower`).

## 7) Аутентификация и роли

**JSON API (`/api/*`):**

- Только заголовок **`X-API-Key`**.
- Ключи: `API_KEY_911` → только тип `911`; `API_KEY_KC` → только `КЦ`; `API_KEY_ADMIN` → оба типа + админские методы.

**Веб‑интерфейс (сессия в cookie):**

- Вход через **LDAP** по переменным `LDAP_*`; роль определяется членством в `LDAP_GROUP_DN_ADMIN` / `LDAP_GROUP_DN_911` / `LDAP_GROUP_DN_KC`.
- Если задан **суперпользователь** (`UI_SUPERUSER_LOGIN` / `UI_SUPERUSER_PASSWORD`), он получает полный доступ **ADMIN** до проверки LDAP.

API‑ключи для HTML‑страниц не используются.

## 8) API (кратко)

- `GET /api/calls` — фильтры: `period`, `date_from`, `date_to`, `manager`, `status`, `call_type`, `limit`, `offset`; **`include_text`** по умолчанию **false** (текст транскрипции не отдаётся, пока не запрошен явно — объём ответа меньше).
- `GET /api/calls/export.xlsx`, `GET /api/classified-calls/export.xlsx` — выгрузки.
- `GET /api/users`, `GET /api/pipeline-runs` (ADMIN), `GET /api/catalog` (ADMIN).

Статусы звонков в API — строковые **коды** из справочника `call_statuses` (см. миграцию `20260402_*`).

## 9) UI

- Таблицы **«Звонки»** и **«Классификация»**: серверная пагинация (**~200 строк на страницу**), порядок по умолчанию по дате на стороне сервера; сортировка по клику на заголовок — **только среди строк текущей страницы**.
- Текст транскрипции в списках **подгружается по «Показать»** отдельным запросом `GET /ui/calls/{id}/active-transcription` (в общий список большой TEXT не тянется).
- Классификация: страница `/classified-calls` (роли КЦ и ADMIN), справочник `/catalog` (ADMIN).

## 10) RAG: справочник тем, синонимы, Qdrant

1. Импорт каталога: `python rag/sync_topic_catalog.py`
2. Синонимы: `python rag/generate_catalog_synonyms.py` (в UI после сохранения записи может ставиться задача в очередь, см. `CATALOG_AUTO_SYNONYMS`).

Подробнее — `README_ARCHITECT.md`.

## 11) Классификация КЦ

Статусы отбора по умолчанию: **`TRANSCRIBED`**, **`CLASSIFICATION_FAILED`**. В процессе: **`CLASSIFYING`** → **`CLASSIFIED`** / **`CLASSIFICATION_FAILED`**.

Рекомендуемый скрипт: `rag/classify_calls_v2.py`. Задачи в проде могут выполняться через **jobs** и воркер **`q.classify`**.

## 12) Миграции PostgreSQL

Примеры (из корня проекта, URI без `+psycopg2` для `psql`):

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/20260305_add_pipeline_code_to_pipeline_runs.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/20260312_add_pipeline_metrics.sql
```

Каталог и классификации: `db/migrations/20260323_*`, при необходимости `20260326_*.sql`.

**Сброс тестовых классификаций КЦ** (после этого для схемы со `status_id` см. комментарий в файле):

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/20260401_reset_call_classifications_and_kc_status.sql
```

**Справочник статусов звонков + переход с `calls.status` на FK** (однократно):

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/20260402_call_statuses_normalize.sql
```

Проверка соответствия ORM и БД: `python db/verify_db_contract.py`.

## 13) CI/CD

В GitLab: этапы **test** (`verify_db_contract`), **build** (Docker image), **deploy** (SSH + `docker compose pull && up`). См. `.gitlab-ci.yml`.
