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
pip install -r requirements.txt
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
- `EMBEDDING_MODEL_PATH` (приоритетный путь к эмбеддингу)
- либо `EMBEDDING_MODEL_SBER_PATH` / `EMBEDDING_MODEL_MINI_PATH` (см. `model_paths.py`)

Для RAG / Qdrant:
- `QDRANT_URL`, `QDRANT_API` (или `QDRANT_API_KEY`), `QDRANT_COLLECTION_NAME` (или `QDRANT_COLLECTION_TOPICS`)

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
- сортировка таблиц **в браузере**: в заголовке каждого столбца кнопка с индикаторами ▲▼ (как в Excel); повторный клик по тому же столбцу меняет направление, выбор другого столбца задаёт новую сортировку (без запросов к серверу)
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
- страница «Классификация» (роли `КЦ` и `ADMIN`): фильтры, причина выбора темы, выгрузка в Excel
- страница «Справочник тем» (ADMIN): редактирование каталога; при сохранении запись синхронизируется в Qdrant

## 7) RAG: справочник тем, синонимы, Qdrant

1. **Импорт справочника в БД и векторную БД** — `rag/sync_topic_catalog.py` (источник по умолчанию: `rag/spravochnik.json`):
   ```bash
   python rag/sync_topic_catalog.py
   ```
2. **Генерация синонимов** — `rag/generate_catalog_synonyms.py` (LLM Gemma, запись в `topic_catalog_entries.synonyms_text`; при настроенном `QDRANT_URL` точки обновляются в Qdrant):
   ```bash
   python rag/generate_catalog_synonyms.py --limit 10
   python rag/generate_catalog_synonyms.py --limit all
   python rag/generate_catalog_synonyms.py --entry-id 11
   ```
3. Скрипты в `rag/old/` считаются устаревшими и не используются в прод-контуре.

Подробнее по архитектуре — `README_ARCHITECT.md`.

## 8) Классификация звонков КЦ

### Статусы и выбор звонков

Функция `get_calls_for_classification` в `db/crud.py` по умолчанию берёт звонки со статусами **`TRANSCRIBED`** и **`CLASSIFICATION_FAILED`** (повторная попытка после ошибки).

Во время обработки скрипт выставляет **`CLASSIFYING`**, при успехе — **`CLASSIFIED`**, при исключении — **`CLASSIFICATION_FAILED`**.

Тип звонка по умолчанию: **`КЦ`** (`--call-type`).

### Рекомендуемый скрипт: `rag/classify_calls_v2.py`

Это текущий вариант для экспериментов и перезапуска: гибридный **ретривел как в** `rag/old/rag_summary_with_qdrant_final_3.py` (семантика из Qdrant + сигнал по ключевым словам из каталога, веса short/long, правило «есть keyword hit → не ниже порога»), плюс **LLM выбирает одну подтему** из shortlist в формате JSON (`decision` = `entry_id` или `OTHER`). Результат пишется в **`call_classifications`** и в **`pipeline_runs`** (`pipeline_code = КЦ_CLASSIFICATION`).

**Запуск:**
```bash
python rag/classify_calls_v2.py --call-type КЦ --limit 200
```

**Артефакты на диске** (по умолчанию, без `--debug-dir`):
`output_audio_benchmark/classification_v2/<дата-время>_run<id_запуска>/` — для каждого звонка JSON, prompt и сырой ответ LLM.

**Переменные окружения:** `DATABASE_URL`, `QDRANT_*`, `GEMMA_MODEL_PATH`, путь к эмбеддингу (см. `.env.example` и `model_paths.py`).

### Альтернатива: `rag/classify_calls.py`

Более новая схема скоринга (лексика + синонимы + негативные ключи + эвристики сценария). Можно использовать для сравнения с v2.

### Связи в БД (что не трогает классификация)

- **`transcriptions`** — не изменяются; классификация ссылается на активную транскрипцию через `transcription_id`.
- **`calls.pipeline_run_id`** — обычно относится к **транскрибации**, скрипты классификации его не перезаписывают.
- **`topic_catalog_entries`** — только чтение (и опционально `catalog_entry_id` в результате).
- **`pipeline_runs`** — создаётся одна запись на запуск скрипта; строки в `call_classifications` ссылаются на неё.

## 9) Сброс тестовых классификаций (миграция SQL)

Чтобы удалить все строки из `call_classifications` и вернуть звонки **типа КЦ** из состояний классификации обратно в **`TRANSCRIBED`**:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/20260401_reset_call_classifications_and_kc_status.sql
```

Миграция обнуляет таблицу классификаций целиком и обновляет только звонки с `call_types.code` в `('КЦ','KЦ','KC')` и статусом `CLASSIFIED`, `CLASSIFICATION_FAILED` или `CLASSIFYING`. Транскрипты, справочник и Qdrant не меняются.

В файле миграции есть закомментированный блок для удаления строк `pipeline_runs` с `pipeline_code = 'КЦ_CLASSIFICATION'` (очистка истории в UI).

## 10) Подготовка к Docker (следующий этап)

- единый env-конфиг,
- точка входа `api_service.main:app`,
- зависимости: **`requirements.txt`**.

Далее: `Dockerfile`, `docker-compose.yml`, CI/CD в GitLab.

## 11) Миграции БД (справочно)

`pipeline_code` в `pipeline_runs`:

```bash
psql "$DATABASE_URL" -f db/migrations/20260305_add_pipeline_code_to_pipeline_runs.sql
```

Метрики pipeline (если ещё не применяли):

```bash
psql "$DATABASE_URL" -f db/migrations/20260312_add_pipeline_metrics.sql
```

Каталог тем и классификации — см. `db/migrations/20260323_add_topic_catalog_and_call_classifications.sql` и при необходимости `20260326_*.sql` в том же каталоге.