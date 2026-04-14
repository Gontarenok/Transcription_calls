# Транскрибация и обработка звонков (КЦ / 911)

Сервис: сканирование аудио, транскрибация Whisper, PostgreSQL, фоновые задачи Celery (транскрибация, классификация, саммаризация, каталог), **FastAPI** + веб-интерфейс с разграничением по ролям.

**Рекомендуемый вход в проде:** reverse-proxy (OAuth2 / OpenID Connect или AD) передаёт в приложение заголовки **`X-Forwarded-Login`**, **`X-Forwarded-Roles`** и/или **`X-Forwarded-Groups`** — без хранения каталога пользователей в приложении. Соответствие **группам AD** настраивается на стороне прокси; значения по умолчанию в коде совпадают с корпоративными группами:

| Группа AD (подстрока в заголовке) | Доступ |
|-----------------------------------|--------|
| `AG-AI calls-Administrators` | Администратор: оба типа звонков, пайплайны, API ADMIN |
| `FG-AI calls CC-Users` | Звонки КЦ, классификация |
| `FG-AI calls CC directory-Users` | КЦ + редактирование справочника тем |
| `FG-AI calls 911-Users` | Звонки 911 |

Имена ролей в **`X-Forwarded-Roles`** (если прокси отдаёт готовый список ролей вместо групп) задаются переменными `TRUSTED_ROLE_*` в `.env` (см. `.env.example`).

Опционально для локальной отладки: **`UI_AUTH_MODE=ldap`** — форма входа и LDAP (см. `.env.example`). В проде обычно **`UI_AUTH_MODE=trusted_headers`**.

---

## 1. Подготовка окружения

1. Python **3.11** (или **≥3.8**; пакет `prometheus-fastapi-instrumentator` 7.x не ставится на Python 3.7).
2. `pip install -r requirements.txt`
3. Скопируйте `.env.example` в `.env` и заполните значения.

Если pip ругается на `\x00` в `requirements.txt` — файл сохранён как **UTF-16**; пересохраните в **UTF-8**.

Если при установке **ReadTimeout** / `No matching distribution found` для пакетов с PyPI — увеличьте таймаут и повторите:  
`pip install --default-timeout=120 -r requirements.txt`  
(при нестабильной сети можно указать зеркало индекса через `pip.conf` или переменную `PIP_INDEX_URL`).

---

## 2. Обязательные и основные переменные `.env`

| Переменная | Назначение |
|------------|------------|
| `DATABASE_URL` | PostgreSQL, формат `postgresql+psycopg2://...` |
| `API_KEY_911`, `API_KEY_KC`, `API_KEY_ADMIN` | Ключи JSON API (**только заголовок `X-API-Key`**, без query) |
| `SESSION_SECRET` | Подпись cookie сессии (для режима формы / LDAP) |
| `REDIS_PASSWORD` | Пароль Redis в Docker Compose (брокер Celery) |
| `FLOWER_BASIC_AUTH` | `логин:пароль` для Flower; удобно совместить с `UI_SUPERUSER_*` |
| `UI_AUTH_MODE` | `trusted_headers` (прод) или `ldap` (отладка) |
| `UI_SUPERUSER_ENABLED` | `1` — форма входа суперпользователя при `trusted_headers` (dev); `0` — только прокси (прод) |
| `UI_SUPERUSER_LOGIN` / `UI_SUPERUSER_PASSWORD` | Локальный полный доступ в UI (если включено выше) |

Cookie за HTTPS: **`SESSION_COOKIE_SECURE=1`** (по умолчанию включено).

Наблюдаемость:

- **`LOG_JSON=1`** — JSON-логи в stdout.
- **`PROMETHEUS_ENABLED=1`** — endpoint **`GET /metrics`**.
- **`OTEL_ENABLED=1`** и **`OTEL_EXPORTER_OTLP_ENDPOINT`** — опционально установите `pip install -r requirements-otel.txt` (см. комментарии в файле из‑за зависимостей protobuf).

Пути к моделям, Qdrant, Celery — см. `.env.example`.

---

## 3. Запуск без Docker

```bash
uvicorn api_service.main:app --host 0.0.0.0 --port 5000
```

- Документация API: `http://<host>:5000/docs`
- UI: `/`, `/calls`, `/classified-calls`, `/users`, `/pipeline-runs` (админ пайплайна), `/catalog` (справочник — админ сервиса или роль редактора справочника КЦ)
- Готовность: **`GET /healthz`**
- Метрики: **`GET /metrics`** (если не отключено)

В каждом ответе присутствует заголовок **`X-Request-ID`** (или передайте свой `X-Request-ID`).

---

## 4. Docker и Docker Compose

Образ: **multi-stage** `Dockerfile`, приложение под пользователем **`appuser`** (uid 1000), **HEALTHCHECK** на `/healthz`.

```bash
docker compose up -d --build
```

| Сервис | Назначение |
|--------|------------|
| `redis` | Брокер Celery, **порт наружу не публикуется**, пароль **`REDIS_PASSWORD`** |
| `web` | FastAPI, порт **5000** |
| `worker-transcribe` | Очередь **`q.transcribe`** |
| `worker-light` | **`q.classify`**, **`q.catalog`**, **`q.summarize`** |
| `flower` | Монитор Celery, порт **5555**, обязательно **`FLOWER_BASIC_AUTH`** |

`CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` в compose задаются с паролем Redis. Пути к моделям и `DATABASE_URL` должны быть доступны из контейнера (при необходимости добавьте **volumes**).

Транскрибация на **GPU:** при необходимости доступа к устройствам NVIDIA смонтируйте runtime в compose и при необходимости переопределите пользователя в образе (по умолчанию не root).

---

## 5. Аутентификация

### JSON API (`/api/*`)

- Только заголовок **`X-API-Key`**.
- Ключи: `API_KEY_911` → только 911; `API_KEY_KC` → только КЦ; `API_KEY_ADMIN` → оба типа + админские методы.

### Веб-интерфейс

- **`trusted_headers`:** логин и роли/группы с каждым запросом; приложение не синхронизирует каталог AD. Если **`UI_SUPERUSER_ENABLED=1`** и заданы **`UI_SUPERUSER_LOGIN`** / **`UI_SUPERUSER_PASSWORD`**, доступна форма входа суперпользователя без заголовков прокси (удобно для dev); у запросов с заголовком логина прокси по-прежнему приоритет.
- **`ldap`:** форма входа; локальный суперпользователь только при **`UI_SUPERUSER_ENABLED=1`** и **`UI_SUPERUSER_*`** в `.env` (так отключается супер в проде при LDAP).

---

## 6. API (кратко)

- `GET /api/calls` — фильтры, **`include_text`** по умолчанию false.
- Экспорт Excel: `/api/calls/export.xlsx`, `/api/classified-calls/export.xlsx`.
- `GET /api/users`, `GET /api/pipeline-runs` (ADMIN), `GET /api/catalog` (ADMIN по API-ключу).

---

## 7. UI

- Пагинация списков (~200 строк), транскрипт подгружается отдельно: `GET /ui/calls/{id}/active-transcription`.
- Классификация КЦ: `/classified-calls` (роли с доступом к КЦ).
- Справочник: `/catalog` — **администратор сервиса** или **редактор справочника КЦ** (группа `FG-AI calls CC directory-Users` / роль `kc_catalog` в заголовке).

---

## 8. RAG, классификация, миграции

- Классификация КЦ (RAG) и каталог: `README_ARCHITECT.md`, `classification_rag/README.md`. Саммаризация 911: `summarization_llm/README.md`.
- **Сканирование папок (КЦ / 911):** длительность файлов считается в `audio_utils.py` — предпочтительно **`ffprobe`** в PATH (входит в состав ffmpeg, есть в Docker-образе приложения); иначе soundfile / librosa. Это сильно влияет на время этапа `scan` на больших объёмах mp3/m4a.
- **БД при скане:** кэшируются id статусов звонка (`get_call_status_by_code`), пересчёт `parts_count` / длительности по частям — агрегатами SQL без загрузки всех строк `call_parts` (`refresh_call_rollups`).
- Миграции PostgreSQL: `db/migrations/*.sql` — порядок и однократные скрипты см. комментарии в файлах.
- Проверка соответствия ORM и БД: `python db/verify_db_contract.py`.
- Для пустой БД можно создать таблицы: `python -c "from db.init_db import create_all; create_all()"`.

---

## 9. CI/CD (GitLab)

Файл `.gitlab-ci.yml`:

- **test:** PostgreSQL service, `wait_for_postgres`, `init_db`, `verify_db_contract`.
- **build:** сборка и push Docker-образа.
- **deploy:** SSH и `docker compose pull && up` на прод.

При необходимости переопределите **`DATABASE_URL`** в GitLab CI/CD Variables.

---

## 10. Резервное копирование

См. **[docs/BACKUP.md](docs/BACKUP.md)** (PostgreSQL, Qdrant, политика хранения).
