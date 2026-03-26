# RAG модуль: что сейчас запускать и в каком порядке

В каталоге `rag/` есть старые экспериментальные скрипты и новый рабочий поток.
Ниже — **актуальный порядок запуска** для текущей архитектуры проекта.

## 1) Подготовка и импорт справочника

Источник от бизнеса: `rag/reference_topics.txt`.

```bash
python rag/sync_topic_catalog.py
```

Что делает:
1. Парсит текстовый справочник.
2. Сохраняет/обновляет записи в БД (`topic_catalog_entries`).
3. Синхронизирует записи в Qdrant.

## 2) (Опционально) автогенерация синонимов

```bash
python rag/generate_catalog_synonyms.py --limit 20
python rag/generate_catalog_synonyms.py --limit 20 --write
```

- без `--write` — только просмотр предложений,
- с `--write` — запись предложений в `synonyms_text`.

## 3) Классификация звонков

```bash
python rag/classify_calls.py --call-type КЦ --limit 200
```

Что делает:
1. Берёт звонки со статусом `TRANSCRIBED`.
2. Ищет кандидатов в Qdrant.
3. Считает гибридный скоринг (semantic + lexical + сценарные сигналы).
4. Даёт shortlist в Gemma (`gemma-3-4b-it`) с жёстким выбором только из кандидатов.
5. Пишет результат в `call_classifications` и обновляет статус звонка.

## Что считать legacy

Скрипты вида `rag_summary_with_qdrant_final*.py` — исторические/экспериментальные.
Для production-пайплайна используйте связку:
- `sync_topic_catalog.py`
- `classify_calls.py`
- (опционально) `generate_catalog_synonyms.py`