from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from celery import shared_task

from db.base import SessionLocal
from db.crud import (
    add_summarization,
    create_pipeline_run,
    finish_pipeline_run,
    get_calls_for_summarization,
    set_call_status,
)
from jobs.pipeline_lifecycle import register_active_pipeline, unregister_active_pipeline
from model_paths import model_settings
from summarization_llm import PROMPT_VERSION, get_text_generator, summarize_transcript_text


PIPELINE_CODE = "911_SUMMARIZATION"


def _get_active_transcription(call) -> Any | None:
    for t in call.transcriptions:
        if t.is_active:
            return t
    return None


@shared_task(name="jobs.summarize_911_batch", bind=True, acks_late=True)
def summarize_911_batch(self, *, limit: int = 100) -> dict:
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
    try:
        get_text_generator()

        calls = get_calls_for_summarization(db, call_type_code="911", limit=int(limit))

        for call in calls:
            transcription = _get_active_transcription(call)
            if not transcription or not (transcription.text or "").strip():
                if not transcription:
                    skipped_no_transcription += 1
                else:
                    skipped_empty_text += 1
                continue

            set_call_status(db, call.id, "SUMMARIZING", error_message=None)
            try:
                summary = summarize_transcript_text(transcription.text.strip())
                add_summarization(
                    db,
                    call_id=call.id,
                    model_name=model_settings.gemma_model_path,
                    prompt_version=PROMPT_VERSION,
                    temperature=None,
                    participants=summary.participants,
                    platform=summary.platform,
                    topic=summary.topic,
                    essence=summary.essence,
                    action_result=summary.action_result,
                    outcome=summary.outcome,
                    short_summary=summary.short_summary,
                    raw_text=summary.raw_text,
                )
                set_call_status(db, call.id, "SUMMARIZED", error_message=None)
                progress["processed"] += 1
            except Exception as exc:
                set_call_status(db, call.id, "SUMMARIZATION_FAILED", error_message=str(exc))

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


@shared_task(name="jobs.summarize_enqueue_pending", bind=True)
def summarize_enqueue_pending(self, *, limit: int = 100) -> dict:
    res = summarize_911_batch.delay(limit=limit)
    return {"status": "enqueued", "task_id": res.id, "limit": int(limit)}
