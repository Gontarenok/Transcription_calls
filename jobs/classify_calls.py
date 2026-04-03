from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import torch
from celery import shared_task
from sentence_transformers import SentenceTransformer
from datetime import datetime, timezone

from db.base import SessionLocal
from db.crud import (
    add_call_classification,
    create_pipeline_run,
    finish_pipeline_run,
    get_active_catalog_entries,
    get_calls_for_classification,
    set_call_status,
)
from jobs.pipeline_lifecycle import register_active_pipeline, unregister_active_pipeline
from model_paths import model_settings
from rag.classify_calls_v2 import (
    Candidate,
    build_prompt,
    choose_result,
    embed_text,
    init_generator,
    normalize_call_text,
    no_signal,
    retrieve_candidates,
    score_candidates_legacy,
)
from rag.catalog_service import init_qdrant


PIPELINE_CODE = "КЦ_CLASSIFICATION"
PROMPT_VERSION = "kc-topic-classifier-v2-legacy-retrieval"
CLASSIFIER_VERSION = "hybrid-rag-legacy-retrieval-2026-03-30"

DEVICE = 0 if torch.cuda.is_available() else -1


@lru_cache(maxsize=1)
def _embedder() -> SentenceTransformer:
    return SentenceTransformer(model_settings.embedding_model_path)


def _debug_dir_for_run(pipeline_run_id: int) -> str:
    base = Path(os.getenv("CLASSIFY_DEBUG_DIR_BASE", "output_audio_benchmark")) / "classification_jobs"
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    return str(base / f"{ts}_run{pipeline_run_id}")


def _get_active_transcription(call) -> Any | None:
    for t in call.transcriptions:
        if t.is_active:
            return t
    return None


@shared_task(name="jobs.classify_kc_batch", bind=True, acks_late=True)
def classify_kc_batch(self, *, limit: int = 200, debug_dir: str | None = None) -> dict:
    """
    Classify a batch of KC calls with statuses TRANSCRIBED/CLASSIFICATION_FAILED.
    Uses the same logic as rag/classify_calls_v2.py but runs as a Celery job.
    """
    start_ts = time.time()
    progress = {"processed": 0}
    skipped_no_transcription = 0
    skipped_empty_text = 0

    db = SessionLocal()
    pipeline_run = create_pipeline_run(
        db,
        started_at=datetime.now(timezone.utc),
        status="RUNNING",
        pipeline_code=PIPELINE_CODE,
    )
    register_active_pipeline(pipeline_run.id, lambda: int(progress["processed"]))
    run_debug_dir = debug_dir or (_debug_dir_for_run(pipeline_run.id) if os.getenv("CLASSIFY_DEBUG", "").strip() else None)

    try:
        catalog_entries = get_active_catalog_entries(db)
        catalog_map = {e.id: e for e in catalog_entries}
        if not catalog_map:
            raise RuntimeError("Catalog is empty. Run rag/sync_topic_catalog.py first.")

        qdrant = init_qdrant()
        embedder = _embedder()
        generator = init_generator()

        calls = get_calls_for_classification(db, call_type_code="КЦ", limit=int(limit))

        for call in calls:
            transcription = _get_active_transcription(call)
            if not transcription or not (transcription.text or "").strip():
                if not transcription:
                    skipped_no_transcription += 1
                else:
                    skipped_empty_text += 1
                continue

            set_call_status(db, call.id, "CLASSIFYING", error_message=None)
            raw_text = transcription.text.strip()
            norm_text = normalize_call_text(raw_text)

            try:
                query_vector = embed_text(embedder, norm_text)
                hits = retrieve_candidates(qdrant, query_vector)
                candidates = score_candidates_legacy(norm_text, hits, catalog_map)

                if not candidates or no_signal(candidates):
                    add_call_classification(
                        db,
                        call_id=call.id,
                        transcription_id=transcription.id,
                        catalog_entry_id=None,
                        pipeline_run_id=pipeline_run.id,
                        model_name="legacy-retrieval-fallback",
                        embedding_model_name=model_settings.embedding_model_path,
                        prompt_version=PROMPT_VERSION,
                        classifier_version=CLASSIFIER_VERSION,
                        spravochnik_version="db-live",
                        decision_mode="no_signal_other",
                        topic_name="Другое",
                        subtopic_name="Другое",
                        confidence=0.0,
                        lexical_score=None,
                        semantic_score=None,
                        rerank_score=None,
                        reasoning="Недостаточно сигнала по семантике и ключевым словам (legacy no_signal).",
                        evidence=[],
                        candidates=[c.__dict__ for c in candidates],
                        raw_llm_output=None,
                    )
                    set_call_status(db, call.id, "CLASSIFIED")
                    progress["processed"] += 1
                    continue

                llm_text = normalize_call_text(raw_text)
                prompt = build_prompt(llm_text, candidates)
                result, raw_llm = choose_result(generator, prompt, candidates)

                decision = result["decision"]
                chosen: Candidate | None = None
                if decision != "OTHER":
                    chosen = next((c for c in candidates[:6] if str(c.entry_id) == str(decision)), None)

                if not chosen:
                    reason = result.get("reason") or (raw_llm.strip()[:500] if raw_llm else None)
                    best = candidates[0] if candidates else None
                    add_call_classification(
                        db,
                        call_id=call.id,
                        transcription_id=transcription.id,
                        catalog_entry_id=None,
                        pipeline_run_id=pipeline_run.id,
                        model_name=model_settings.gemma_model_path,
                        embedding_model_name=model_settings.embedding_model_path,
                        prompt_version=PROMPT_VERSION,
                        classifier_version=CLASSIFIER_VERSION,
                        spravochnik_version="db-live",
                        decision_mode="llm_other",
                        topic_name="Другое",
                        subtopic_name="Другое",
                        confidence=float(result.get("confidence") or 0.0),
                        lexical_score=best.lexical_score if best else None,
                        semantic_score=best.semantic_score if best else None,
                        rerank_score=best.rerank_score if best else None,
                        reasoning=reason,
                        evidence=result.get("evidence") or [],
                        candidates=[c.__dict__ for c in candidates],
                        raw_llm_output=raw_llm,
                    )
                    set_call_status(db, call.id, "CLASSIFIED")
                    progress["processed"] += 1
                    continue

                reason = result.get("reason") or (raw_llm.strip()[:500] if raw_llm else None)
                add_call_classification(
                    db,
                    call_id=call.id,
                    transcription_id=transcription.id,
                    catalog_entry_id=chosen.entry_id,
                    pipeline_run_id=pipeline_run.id,
                    model_name=model_settings.gemma_model_path,
                    embedding_model_name=model_settings.embedding_model_path,
                    prompt_version=PROMPT_VERSION,
                    classifier_version=CLASSIFIER_VERSION,
                    spravochnik_version="db-live",
                    decision_mode="llm_choice",
                    topic_name=chosen.topic,
                    subtopic_name=chosen.subtopic,
                    confidence=float(result.get("confidence") or 0.0),
                    lexical_score=chosen.lexical_score,
                    semantic_score=chosen.semantic_score,
                    rerank_score=chosen.rerank_score,
                    reasoning=reason,
                    evidence=result.get("evidence") or [],
                    candidates=[c.__dict__ for c in candidates],
                    raw_llm_output=raw_llm,
                )
                set_call_status(db, call.id, "CLASSIFIED")
                progress["processed"] += 1

            except Exception as exc:
                set_call_status(db, call.id, "CLASSIFICATION_FAILED", error_message=str(exc))

        finish_pipeline_run(
            db,
            pipeline_run_id=pipeline_run.id,
            status="SUCCESS",
            finished_at=datetime.now(timezone.utc),
            processed_calls=int(progress["processed"]),
            duration_seconds=int(time.time() - start_ts),
            error_message=None,
        )
        return {
            "status": "ok",
            "pipeline_run_id": pipeline_run.id,
            "processed": progress["processed"],
            "skipped_no_transcription": skipped_no_transcription,
            "skipped_empty_text": skipped_empty_text,
            "debug_dir": run_debug_dir,
        }
    except Exception as exc:
        finish_pipeline_run(
            db,
            pipeline_run_id=pipeline_run.id,
            status="FAILED",
            finished_at=datetime.now(timezone.utc),
            processed_calls=int(progress["processed"]),
            duration_seconds=int(time.time() - start_ts),
            error_message=str(exc),
        )
        raise
    finally:
        unregister_active_pipeline(pipeline_run.id)
        db.close()


@shared_task(name="jobs.classify_enqueue_pending", bind=True)
def classify_enqueue_pending(self, *, limit: int = 200) -> dict:
    """
    Lightweight scheduler task: enqueue one classify_kc_batch.
    Intended to be triggered by systemd timer or Celery Beat.
    """
    res = classify_kc_batch.delay(limit=limit)
    return {"status": "enqueued", "task_id": res.id, "limit": int(limit)}

