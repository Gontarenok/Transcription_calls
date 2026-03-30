from __future__ import annotations

import argparse
import json
import re

import torch
from transformers import pipeline

from db.base import SessionLocal
from db.crud import list_topic_catalog_entries, set_catalog_qdrant_point_id, update_topic_catalog_entry
from model_paths import model_settings
from rag.catalog_service import qdrant_enabled, sync_catalog_entries

DEVICE = 0 if torch.cuda.is_available() else -1
PROMPT_VERSION = "catalog-synonyms-v1"


def parse_json_list(raw: str) -> list[str]:
    # Markdown code block
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, flags=re.S | re.I)
    if fence:
        try:
            values = json.loads(fence.group(1))
            if isinstance(values, list):
                return _dedupe_str_list(values)
        except Exception:
            pass
    match = re.search(r"\[.*\]", raw, flags=re.S)
    if match:
        try:
            values = json.loads(match.group(0))
        except Exception:
            values = None
        if isinstance(values, list):
            return _dedupe_str_list(values)[:12]

    # Fallback: model may return plain newline-separated phrases instead of JSON.
    text = (raw or "").strip()
    parts = text.splitlines()
    # Some models return literal "\n" sequences instead of real newlines.
    if len(parts) <= 1 and "\\n" in text:
        parts = [p for p in text.split("\\n")]

    line_items: list[str] = []
    for line in parts:
        item = line.strip()
        if not item:
            continue
        # Remove bullets/numbering like "-", "1.", "1)".
        item = re.sub(r"^\s*(?:[-*•]\s+|\d+[\.\)]\s+)", "", item).strip()
        # Skip obvious non-content/service lines.
        low = item.lower()
        if any(tok in low for tok in ["both max_new_tokens", "please refer", "http", "process finished", "loading weights"]):
            continue
        if len(item) < 3:
            continue
        line_items.append(item)
    return _dedupe_str_list(line_items)[:12]


def _dedupe_str_list(values: list) -> list[str]:
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def build_prompt(topic: str, subtopic: str, description: str, keywords_text: str) -> str:
    return f"""
Ты помогаешь расширять справочник тем для классификации звонков.
Нужно предложить варианты синонимов и типовых фраз для одной конкретной подтемы.
Не меняй тему. Не добавляй фразы, которые могут относиться к другим подтемам.
Отдавай только JSON-массив строк без пояснений.
Нужно 5-12 фраз.

Тема: {topic}
Подтема: {subtopic}
Описание: {description}
Ключевые слова:
{keywords_text}
""".strip()


def main():
    parser = argparse.ArgumentParser(description="Автоматически предлагает синонимы/варианты фраз для записей справочника")
    parser.add_argument("--limit", default="10", help="Сколько записей обработать: число или 'all'")
    parser.add_argument("--entry-id", type=int, default=None, help="Сгенерировать синонимы для одной записи справочника по ID")
    args = parser.parse_args()

    generator = pipeline(
        "text-generation",
        model=model_settings.gemma_model_path,
        tokenizer=model_settings.gemma_model_path,
        device=DEVICE,
        torch_dtype="auto",
        # model/model tokenizer are local paths; passing local_files_only here
        # can be double-applied by pipeline internals on some transformers versions.
    )

    # У модели в generation_config часто max_length=20: тогда суммарная длина (prompt+ответ)
    # режется до 20 токенов — JSON не помещается, parse_json_list возвращает [].
    # Оставляем только max_new_tokens на вызове; снимаем конфликтующий max_length.
    try:
        gc = generator.model.generation_config
        if getattr(gc, "max_length", None) is not None:
            gc.max_length = None
    except Exception:
        pass

    db = SessionLocal()
    try:
        updated_entries = []
        if args.entry_id is not None:
            rows = [row for row in list_topic_catalog_entries(db, include_inactive=True) if row.id == args.entry_id]
        else:
            rows = list_topic_catalog_entries(db, include_inactive=False)
            if str(args.limit).strip().lower() != "all":
                rows = rows[: int(args.limit)]

        for row in rows:
            t_low = (row.topic_name or "").strip().lower()
            s_low = (row.subtopic_name or "").strip().lower()
            if t_low == "другое" and s_low == "другое":
                print(f"\n[{row.id}] {row.topic_name} / {row.subtopic_name} — пропуск (служебная тема OTHER)")
                continue
            if not (row.keywords_text or "").strip():
                print(f"\n[{row.id}] {row.topic_name} / {row.subtopic_name} — пропуск (нет ключевых слов)")
                continue

            prompt = build_prompt(row.topic_name, row.subtopic_name, row.description, row.keywords_text)
            raw = generator(
                prompt,
                max_new_tokens=180,
                do_sample=False,
                return_full_text=False,
            )[0]["generated_text"]
            suggestions = parse_json_list(raw)
            print(f"\n[{row.id}] {row.topic_name} / {row.subtopic_name}")
            print("Предложенные синонимы:")
            for item in suggestions:
                print(" -", item)
            if not suggestions:
                preview = raw.strip().replace("\n", "\\n")
                print("RAW (first 350 chars):", preview[:350])
            # Always overwrite synonyms on each generation run.
            entry = update_topic_catalog_entry(
                db,
                entry_id=row.id,
                topic_name=row.topic_name,
                subtopic_name=row.subtopic_name,
                description=row.description,
                keywords_text=row.keywords_text,
                synonyms_text="\n".join(suggestions) if suggestions else None,
                negative_keywords_text=row.negative_keywords_text,
                is_active=row.is_active,
            )
            print(f"Saved to DB: synonyms={len(suggestions)}")
            updated_entries.append(entry)

        if updated_entries and qdrant_enabled():
            point_ids = sync_catalog_entries(updated_entries)
            for entry, point_id in zip(updated_entries, point_ids):
                set_catalog_qdrant_point_id(db, entry.id, point_id)
            print(f"\nSynced to Qdrant: {len(point_ids)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()