Проект для обработки звонков (КЦ/911):
- сканирование файлов звонков,
- транскрибация Whisper,
- хранение звонков/частей/транскриптов в Postgres,
- FastAPI сервис с доступом по ролям,
- UI для фильтрации и выгрузки в Excel.

## 1) Подготовка окружения (PyCharm / локально)

1. Создайте виртуальное окружение Python.
2. Установите зависимости:

```bash
pip install -r requirements_api.txt
```

3. Скопируйте `.env.example` в `.env` и заполните значения.

## 2) Параметры `.env`

Обязательные:
- `DATABASE_URL`
- `API_KEY_911`
- `API_KEY_KC`
- `API_KEY_ADMIN`

Сетевые параметры API:
- `APP_HOST`
- `APP_PORT`

Глобальные пути моделей (для переиспользования между проектами):
- `WHISPER_MODELS_ROOT`
- `WHISPER_MODEL_DEFAULT`
- `WHISPER_MODEL_SMALL`
- `WHISPER_MODEL_MEDIUM`
- `WHISPER_MODEL_LARGE`
- `GEMMA_MODEL_PATH`
- `EMBEDDING_MODEL_PATH`

## 3) Запуск FastAPI

```bash
uvicorn api_service.main:app --host 0.0.0.0 --port 5000
```

После запуска:
- Swagger: `http://<host>:5000/docs`
- Страница входа UI: `http://<host>:5000/`
- Основная таблица звонков: `http://<host>:5000/calls`
- Страница пользователей: `http://<host>:5000/users`
- Страница логов пайплайнов (только ADMIN): `http://<host>:5000/pipeline-runs`

## 4) Аутентификация и роли

Роли и ключи:
- `API_KEY_911` → доступ только к звонкам типа `911`
- `API_KEY_KC` → доступ только к звонкам типа `КЦ`
- `API_KEY_ADMIN` → доступ к обоим типам

Для API используйте header `X-API-Key`.
Для UI ключ вводится на странице входа `/` и передаётся в форму.

## 5) API методы

- `GET /api/calls`
  - фильтры: `period`, `date_from`, `date_to`, `manager`, `status`, `call_type`, `limit`, `offset`
  - `include_text=true` по умолчанию (возвращает активную транскрипцию сразу)
  - `all_records=true` — вернуть все строки по фильтру (без limit/offset)
  - `total` в ответе помогает строить пагинацию на стороне клиента
- `GET /api/calls/{call_id}`
- `GET /api/calls/export.xlsx` — выгрузка текущей фильтрации в Excel
- `GET /api/users` — пользователи (`id`, `full_name`, `domain`, `department`) с ролевой фильтрацией
- `GET /api/pipeline-runs` — логи запусков пайплайнов (только ADMIN), включая `total_audio_seconds` и `avg_rtf`

## 6) UI возможности

- вход по API-ключу (поле как пароль + кнопка “глаз”)
- фильтры:
  - период: сегодня/вчера/текущая неделя/прошлая/текущий месяц/прошлый
  - кастомные даты (дни, без времени)
  - менеджер (выпадающий список по ФИО)
  - статус (выпадающий список)
  - тип звонка
- выгрузка текущей выборки в Excel
- пагинация в UI по звонкам
- отдельная страница пользователей с ролевым ограничением по департаментам
- отдельная страница логов запусков пайплайнов (только ADMIN)

## 7) Подготовка к Docker (следующий этап)

Проект уже подготовлен к контейнеризации:
- единый env-конфиг,
- API входная точка `api_service.main:app`,
- зависимости в `requirements_api.txt`.

Далее можно добавить `Dockerfile`, `docker-compose.yml` и CI/CD в GitLab.


## 8) Миграция БД для `pipeline_runs.pipeline_code`

Чтобы разделять логи пайплайнов 911 и КЦ, добавлен столбец `pipeline_code` в таблицу `pipeline_runs`.

Если БД уже создана, выполните SQL-миграцию:

```bash
psql "$DATABASE_URL" -f db/migrations/20260305_add_pipeline_code_to_pipeline_runs.sql
```

> Миграция безопасна для существующих данных: столбец добавляется с дефолтом `UNKNOWN` и индексом.


Дополнительная миграция (метрики pipeline):

```bash
psql "$DATABASE_URL" -f db/migrations/20260306_add_active_and_pipeline_metrics.sql
```