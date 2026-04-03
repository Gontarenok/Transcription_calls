from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import torch
from celery import shared_task
from transformers import pipeline

from db.base import SessionLocal
from db.crud import add_summarization, create_pipeline_run, finish_pipeline_run, get_calls_for_summarization, set_call_status
from model_paths import model_settings


PIPELINE_CODE = "911_SUMMARIZATION"
PROMPT_VERSION = "911-summarizer-v1"
DEVICE = 0 if torch.cuda.is_available() else -1


@dataclass
class Summary:
    participants: str | None
    platform: str | None
    topic: str | None
    essence: str | None
    action_result: str | None
    outcome: str | None
    short_summary: str | None
    raw_text: str | None


@lru_cache(maxsize=1)
def _generator():
    gen = pipeline(
        "text-generation",
        model=model_settings.gemma_model_path,
        tokenizer=model_settings.gemma_model_path,
        device=DEVICE,
        torch_dtype="auto",
    )
    try:
        gc = gen.model.generation_config
        if getattr(gc, "max_length", None) is not None:
            gc.max_length = None
        if hasattr(gc, "do_sample"):
            gc.do_sample = False
    except Exception:
        pass
    return gen


def _get_active_transcription(call) -> Any | None:
    for t in call.transcriptions:
        if t.is_active:
            return t
    return None


def _safe_json_load(raw: str) -> dict[str, Any] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    matches = list(re.finditer(r"\{.*?\}", raw, flags=re.S))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].group(0))
    except Exception:
        return None


def _build_prompt(transcript_text: str) -> str:
    return f"""
Ты — ассистент, который делает краткую структурированную выжимку звонка внутренней технической поддержки (911).
Верни ответ СТРОГО в JSON без markdown и без пояснений вокруг.
Схема:
{{
  "participants": "кто с кем разговаривает (если понятно, иначе null)",
  "platform": "канал/система/продукт (если понятно, иначе null)",
  "topic": "краткая тема обращения",
  "essence": "суть проблемы (1-3 предложения)",
  "action_result": "какие действия предприняты/что сделали",
  "outcome": "чем закончилось (если неизвестно — null)",
  "short_summary": "1 предложение итоговой выжимки"
}}

Текст разговора:
{transcript_text}
""".strip()


def _parse_summary(raw_llm: str) -> Summary:
    payload = _safe_json_load(raw_llm) or {}
    def _s(key: str) -> str | None:
        v = payload.get(key)
        if v is None:
            return None
        text = str(v).strip()
        return text or None
    return Summary(
        participants=_s("participants"),
        platform=_s("platform"),
        topic=_s("topic"),
        essence=_s("essence"),
        action_result=_s("action_result"),
        outcome=_s("outcome"),
        short_summary=_s("short_summary"),
        raw_text=raw_llm.strip()[:20000] if raw_llm else None,
    )


@shared_task(name="jobs.summarize_911_batch", bind=True, acks_late=True)
def summarize_911_batch(self, *, limit: int = 100) -> dict:
    start_ts = time.time()
    processed = 0
    skipped_no_transcription = 0
    skipped_empty_text = 0

    db = SessionLocal()
    pipeline_run = create_pipeline_run(
        db,
        started_at=datetime.now(timezone.utc),
        status="RUNNING",
        pipeline_code=PIPELINE_CODE,
    )
    try:
        gen = _generator()
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
                prompt = _build_prompt(transcription.text.strip())
                raw = gen(prompt, max_new_tokens=420, do_sample=False, return_full_text=False)[0]["generated_text"]
                summary = _parse_summary(raw)
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
                processed += 1
            except Exception as exc:
                set_call_status(db, call.id, "SUMMARIZATION_FAILED", error_message=str(exc))

        finish_pipeline_run(
            db,
            pipeline_run_id=pipeline_run.id,
            status="SUCCESS",
            finished_at=datetime.now(timezone.utc),
            processed_calls=int(processed),
            duration_seconds=int(time.time() - start_ts),
            error_message=None,
        )
        return {
            "status": "ok",
            "pipeline_run_id": pipeline_run.id,
            "processed": processed,
            "skipped_no_transcription": skipped_no_transcription,
            "skipped_empty_text": skipped_empty_text,
        }
    except Exception as exc:
        finish_pipeline_run(
            db,
            pipeline_run_id=pipeline_run.id,
            status="FAILED",
            finished_at=datetime.now(timezone.utc),
            processed_calls=int(processed),
            duration_seconds=int(time.time() - start_ts),
            error_message=str(exc),
        )
        raise
    finally:
        db.close()


@shared_task(name="jobs.summarize_enqueue_pending", bind=True)
def summarize_enqueue_pending(self, *, limit: int = 100) -> dict:
    res = summarize_911_batch.delay(limit=limit)
    return {"status": "enqueued", "task_id": res.id, "limit": int(limit)}

