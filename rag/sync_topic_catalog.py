from __future__ import annotations

import argparse
from pathlib import Path

from db.base import SessionLocal
from db.crud import (
    mark_missing_catalog_entries_inactive,
    set_catalog_qdrant_point_id,
    upsert_topic_catalog_entry,
)
from rag.catalog_service import build_doc_text, entry_source_hash, sync_catalog_entries
from rag.convert_spravochnik import convert as convert_txt_to_json


def load_records_from_reference(reference_path: Path, json_path: Path) -> list[dict]:
    convert_txt_to_json(reference_path, json_path)
    import json

    return json.loads(json_path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Импортирует справочник тем/подтем в БД и синхронизирует его с Qdrant")
    parser.add_argument("--reference", default="reference_topics.txt")
    parser.add_argument("--json-cache", default="spravochnik.generated.json")
    args = parser.parse_args()

    reference_path = Path(args.reference)
    json_path = Path(args.json_cache)
    if not reference_path.exists():
        raise SystemExit(f"Не найден справочник: {reference_path}")

    records = load_records_from_reference(reference_path, json_path)
    db = SessionLocal()
    try:
        active_pairs: set[tuple[str, str]] = set()
        synced_entries = []
        for record in records:
            topic_name = record.get("topic", "").strip()
            subtopic_name = record.get("subtopic", "").strip()
            description = record.get("description", "").strip()
            keywords = record.get("keywords", []) or []
            keywords_text = "\n".join(str(item).strip() for item in keywords if str(item).strip())
            doc_text = build_doc_text(topic_name, subtopic_name, description, keywords_text)
            source_hash = entry_source_hash(topic_name, subtopic_name, description, keywords_text, None)

            entry = upsert_topic_catalog_entry(
                db,
                topic_name=topic_name,
                subtopic_name=subtopic_name,
                description=description,
                keywords_text=keywords_text,
                synonyms_text=None,
                negative_keywords_text=None,
                source_name=reference_path.name,
                source_hash=source_hash,
                is_active=True,
            )
            entry.doc_text = doc_text
            db.commit()
            db.refresh(entry)
            active_pairs.add((topic_name, subtopic_name))
            synced_entries.append(entry)

        mark_missing_catalog_entries_inactive(db, active_pairs=active_pairs, source_name=reference_path.name)

        point_ids = sync_catalog_entries(synced_entries)
        for entry, point_id in zip(synced_entries, point_ids):
            set_catalog_qdrant_point_id(db, entry.id, point_id)

        print(f"Импортировано/обновлено записей: {len(synced_entries)}")
        print(f"Синхронизировано с Qdrant: {len(point_ids)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()