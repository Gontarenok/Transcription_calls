# summarization_llm

Код **саммаризации** звонков (сейчас 911): промпт, разбор JSON из ответа модели, вызов Gemma.

- Задачи Celery и запись в таблицу `summarizations` — в `jobs/summarize_911.py`.
- Классификация КЦ (RAG, Qdrant, справочник) — в каталоге `classification_rag/`.
- Исторические эксперименты с Qdrant для «summary» лежат в `classification_rag/old/` (не путать с прод-потоком 911).
