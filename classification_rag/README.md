# classification_rag

Каталог **классификации звонков КЦ** (RAG: Qdrant, справочник тем, Gemma для выбора темы/подтемы).

В каталоге `classification_rag/` есть старые экспериментальные скрипты (`old/`) и рабочий поток синхронизации каталога + классификации.

## Быстрый путь

Источник от бизнеса: `classification_rag/reference_topics.txt`.

```bash
python classification_rag/sync_topic_catalog.py
```

Опционально — синонимы в каталоге:

```bash
python classification_rag/generate_catalog_synonyms.py --limit 20
python classification_rag/generate_catalog_synonyms.py --limit all
```

Классификация из CLI:

```bash
python classification_rag/classify_calls.py --call-type КЦ --limit 200
```

В проде чаще используется Celery: `jobs.classify_calls`.

Скрипты вида `classification_rag/old/rag_summary_with_qdrant_final*.py` — исторические/экспериментальные (не путать с саммаризацией 911 в `summarization_llm/`).
