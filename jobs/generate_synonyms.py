from __future__ import annotations

import os
from functools import lru_cache

import torch
from celery import shared_task
from transformers import pipeline

from db.base import SessionLocal
from db.crud import get_topic_catalog_entry, set_catalog_qdrant_point_id, update_topic_catalog_entry
from model_paths import model_settings
from classification_rag.catalog_service import qdrant_enabled, sync_catalog_entries
from classification_rag.generate_catalog_synonyms import build_prompt, parse_json_list


DEVICE = 0 if torch.cuda.is_available() else -1
PROMPT_VERSION = "catalog-synonyms-v1"


@lru_cache(maxsize=1)
def _generator():
    gen = pipeline(
        "text-generation",
        model=model_settings.gemma_model_path,
        tokenizer=model_settings.gemma_model_path,
        device=DEVICE,
        torch_dtype="auto",
    )
    # Avoid accidental truncation from model defaults.
    try:
        gc = gen.model.generation_config
        if getattr(gc, "max_length", None) is not None:
            gc.max_length = None
        if hasattr(gc, "do_sample"):
            gc.do_sample = False
    except Exception:
        pass
    return gen


@shared_task(name="jobs.catalog_generate_synonyms", bind=True, acks_late=True)
def catalog_generate_synonyms(self, entry_id: int) -> dict:
    """
    Generates synonyms for one catalog entry and persists them to DB (+sync to Qdrant if enabled).
    Idempotent: overwrites synonyms_text for the given entry.
    """
    if not entry_id:
        raise ValueError("entry_id is required")

    db = SessionLocal()
    try:
        row = get_topic_catalog_entry(db, int(entry_id))
        if not row:
            return {"status": "skipped", "reason": "not_found", "entry_id": int(entry_id)}
        if (row.topic_name or "").strip().lower() == "другое" and (row.subtopic_name or "").strip().lower() == "другое":
            return {"status": "skipped", "reason": "service_other", "entry_id": row.id}
        if not (row.keywords_text or "").strip():
            return {"status": "skipped", "reason": "no_keywords", "entry_id": row.id}

        prompt = build_prompt(row.topic_name, row.subtopic_name, row.description, row.keywords_text)
        gen = _generator()
        raw = gen(prompt, max_new_tokens=180, do_sample=False, return_full_text=False)[0]["generated_text"]
        suggestions = parse_json_list(raw)

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

        point_id = None
        if qdrant_enabled():
            point_ids = sync_catalog_entries([entry])
            if point_ids:
                point_id = point_ids[0]
                set_catalog_qdrant_point_id(db, entry.id, point_id)

        return {
            "status": "ok",
            "entry_id": entry.id,
            "synonyms_count": len(suggestions),
            "qdrant_synced": bool(point_id),
            "prompt_version": PROMPT_VERSION,
        }
    finally:
        db.close()

