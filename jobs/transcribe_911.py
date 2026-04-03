from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import portalocker
from celery import shared_task

from db.base import SessionLocal
from db.crud import create_pipeline_run, finish_pipeline_run
from jobs.pipeline_lifecycle import count_calls_linked_to_pipeline, register_active_pipeline, unregister_active_pipeline
from process_911_calls_spikers import collect_911_calls_metadata, transcribe_911_calls


PIPELINE_CODE = "911"


def _lock_path() -> str:
    base = os.getenv("JOB_LOCK_DIR", "locks")
    Path(base).mkdir(parents=True, exist_ok=True)
    return str(Path(base) / "n911.lock")


@shared_task(name="jobs.transcribe_911_run", bind=True, acks_late=True)
def transcribe_911_run(
    self,
    *,
    root: str | None = None,
    model: str = "medium",
    recursive: bool = False,
    limit: int = 100000,
) -> dict:
    root_dir = Path(root or os.getenv("N911_ROOT", r"C:\Audio_share\Night"))
    lock_file = _lock_path()

    with portalocker.Lock(lock_file, timeout=0):
        db = SessionLocal()
        run = create_pipeline_run(
            db,
            started_at=datetime.now(timezone.utc),
            status="RUNNING",
            pipeline_code=PIPELINE_CODE,
        )
        register_active_pipeline(run.id, lambda: count_calls_linked_to_pipeline(run.id))
        try:
            collect_911_calls_metadata(db, root_dir=root_dir, recursive=bool(recursive))
            stats = transcribe_911_calls(db, model_size=model, limit=int(limit), pipeline_run_id=run.id)
            finish_pipeline_run(
                db,
                pipeline_run_id=run.id,
                status="SUCCESS",
                finished_at=datetime.now(timezone.utc),
                processed_calls=int(stats.get("transcribed") or 0),
                duration_seconds=None,
                error_message=None,
                total_audio_seconds=float(stats.get("total_audio_seconds") or 0.0),
                avg_rtf=float(stats.get("avg_rtf")) if stats.get("avg_rtf") is not None else None,
            )
            return {"status": "ok", "pipeline_run_id": run.id, **stats}
        except Exception as exc:
            finish_pipeline_run(
                db,
                pipeline_run_id=run.id,
                status="FAILED",
                finished_at=datetime.now(timezone.utc),
                processed_calls=0,
                duration_seconds=None,
                error_message=str(exc),
            )
            raise
        finally:
            unregister_active_pipeline(run.id)
            db.close()


