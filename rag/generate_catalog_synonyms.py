from __future__ import annotations

import argparse
import json
import re

import torch
from transformers import pipeline

from db.base import SessionLocal
from db.crud import list_topic_catalog_entries, update_topic_catalog_entry
from model_paths import model_settings

DEVICE = 0 if torch.cuda.is_available() else -1
PROMPT_VERSION = "catalog-synonyms-v1"


def parse_json_list(raw: str) -> list[str]:
    match = re.search(r"\[.*\]", raw, flags=re.S)
    if not match:
        return []
    try:
        values = json.loads(match.group(0))
    except Exception:
        return []
    result = []
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result[:12]


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
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--write", action="store_true", help="Сохранить результат в БД")
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

    db = SessionLocal()
    try:
        rows = list_topic_catalog_entries(db, include_inactive=False)[: args.limit]
        for row in rows:
            prompt = build_prompt(row.topic_name, row.subtopic_name, row.description, row.keywords_text)
            raw = generator(prompt, max_new_tokens=180, do_sample=False)[0]["generated_text"]
            suggestions = parse_json_list(raw)
            print(f"\n[{row.id}] {row.topic_name} / {row.subtopic_name}")
            print("Предложенные синонимы:")
            for item in suggestions:
                print(" -", item)
            if args.write and suggestions:
                merged = []
                existing = [line.strip() for line in (row.synonyms_text or "").splitlines() if line.strip()]
                for item in existing + suggestions:
                    if item not in merged:
                        merged.append(item)
                update_topic_catalog_entry(
                    db,
                    entry_id=row.id,
                    topic_name=row.topic_name,
                    subtopic_name=row.subtopic_name,
                    description=row.description,
                    keywords_text=row.keywords_text,
                    synonyms_text="\n".join(merged),
                    negative_keywords_text=row.negative_keywords_text,
                    is_active=row.is_active,
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()