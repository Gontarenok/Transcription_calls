from __future__ import annotations

import argparse
from pathlib import Path

from db.base import SessionLocal
from db.crud import (
    mark_missing_catalog_entries_inactive,
    mark_missing_catalog_entries_inactive_entries,
    set_catalog_qdrant_point_id,
    upsert_topic_catalog_entry,
)
from rag.catalog_service import build_doc_text, entry_source_hash, sync_catalog_entries


def load_records_from_spravochnik_json(spravochnik_path: Path) -> list[dict]:
    import json

    return json.loads(spravochnik_path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Импортирует справочник тем/подтем в БД и синхронизирует его с Qdrant")
    parser.add_argument("--spravochnik", default="spravochnik.json", help="JSON справочник (с описаниями), источник истины на этапе тестирования")
    args = parser.parse_args()

    spravochnik_path = Path(args.spravochnik)
    if not spravochnik_path.exists():
        raise SystemExit(f"Не найден справочник: {spravochnik_path}")

    records = load_records_from_spravochnik_json(spravochnik_path)
    db = SessionLocal()
    try:
        active_pairs: set[tuple[str, str]] = set()
        synced_entries = []
        for record in records:
            topic_name = record.get("topic", "").strip()
            subtopic_name = record.get("subtopic", "").strip()
            description = record.get("description", "").strip()
            keywords = record.get("keywords", []) or []
            synonyms = record.get("synonyms", []) or []
            negative_keywords = record.get("negative_keywords", []) or record.get("negative", []) or []
            keywords_text = "\n".join(str(item).strip() for item in keywords if str(item).strip())
            synonyms_text = "\n".join(str(item).strip() for item in synonyms if str(item).strip()) or None
            negative_keywords_text = "\n".join(str(item).strip() for item in negative_keywords if str(item).strip()) or None

            doc_text = build_doc_text(topic_name, subtopic_name, description, keywords_text, synonyms_text)
            source_hash = entry_source_hash(topic_name, subtopic_name, description, keywords_text, synonyms_text)

            entry = upsert_topic_catalog_entry(
                db,
                topic_name=topic_name,
                subtopic_name=subtopic_name,
                description=description,
                keywords_text=keywords_text,
                synonyms_text=synonyms_text,
                negative_keywords_text=negative_keywords_text,
                source_name=spravochnik_path.name,
                source_hash=source_hash,
                is_active=True,
            )
            entry.doc_text = doc_text
            db.commit()
            db.refresh(entry)
            active_pairs.add((topic_name, subtopic_name))
            synced_entries.append(entry)

        changed_active_flags = mark_missing_catalog_entries_inactive_entries(
            db,
            active_pairs=active_pairs,
            source_name=spravochnik_path.name,
        )

        qdrant_sync_entries = synced_entries + [e for e in changed_active_flags if e.id not in {x.id for x in synced_entries}]
        point_ids = sync_catalog_entries(qdrant_sync_entries)
        for entry, point_id in zip(qdrant_sync_entries, point_ids):
            set_catalog_qdrant_point_id(db, entry.id, point_id)

        print(f"Импортировано/обновлено записей: {len(synced_entries)}")
        print(f"Изменено флагов активности: {len(changed_active_flags)}")
        print(f"Синхронизировано с Qdrant: {len(point_ids)}")
        if len(point_ids) == 0 and len(synced_entries) > 0:
            print("⚠️ Qdrant синхронизация вернула 0. Проверьте переменные окружения: QDRANT_URL, QDRANT_API, QDRANT_COLLECTION_NAME.")
    finally:
        db.close()


if __name__ == "__main__":
    main()