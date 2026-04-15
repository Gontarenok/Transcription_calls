# summarization_llm

## Прод-поток 911 (БД → Excel → Work)

1. **Транскрибация** и запись в PostgreSQL — `process_911_calls_spikers.py` (вызывается из `run_911_pipeline.py`).
2. **Саммари** (Gemma, JSON → поля `summarizations`) — `gemma_911_summarizer.py`, пакетный запуск `jobs/summarize_911.py` / `run_summarize_911_batch`.
3. **Полный цикл (по умолчанию)** — из корня репозитория:
   ```bash
   python run_911_pipeline.py
   ```
   Это режим **`full`**: скан → транскрибация → саммари → Excel → Work. Отдельные этапы: `--mode scan`, `--mode transcribe`, `--mode summarize`. Отчётная неделя без дат — **предыдущая пн–вс** (`previous_iso_week_mon_sun`). Свой период: `--period-start` / `--period-end`. Без Work: `--skip-work`.

4. **Агрегаты** сохраняются в таблицу **`weekly_911_reports`** (счётчики итогов, текст задачи, `work_task_id`, путь к Excel). Технические шаги дополнительно в **`pipeline_runs`** (`911`, `911_SUMMARIZATION`, `911_WEEKLY`).

## Файловые скрипты (legacy, без БД)

- `transcribe_audio.py`, `summarize_gemma.py`, `export_summary_to_excel.py`, `build_report.py` — первый прототип на папках `output_*`.
- `run_summariz.py` — только подсказка; используйте `run_911_pipeline.py` (режим `full` по умолчанию).

## Модули

| Файл | Назначение |
|------|------------|
| `gemma_911_summarizer.py` | Промпт, парсинг JSON, нормализация итога |
| `outcome_normalize.py` | Категории «Помогли / Не помогли / В работе / Не указано» |
| `excel_from_db.py` | Excel из звонков с саммари |
| `weekly_stats.py` | Текст задачи и агрегаты по списку звонков |
| `work_client.py` | API Work (см. `WORK_*` в `.env.example`) |
| `report_week_range.py` | Границы отчётной недели и перевод в UTC |

Классификация КЦ (RAG) — каталог **`classification_rag/`**.
