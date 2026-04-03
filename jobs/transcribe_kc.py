from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import portalocker
from celery import chord, shared_task

from db.base import SessionLocal
from db.crud import create_pipeline_run, finish_pipeline_run
from process_kc_calls_spikers import collect_kc_calls_metadata, normalize_day_folder, transcribe_kc_calls


PIPELINE_CODE = "КЦ"


def _lock_path(day_folder: str) -> str:
    base = os.getenv("JOB_LOCK_DIR", "locks")
    Path(base).mkdir(parents=True, exist_ok=True)
    return str(Path(base) / f"kc_{day_folder}.lock")


@shared_task(name="jobs.transcribe_kc_manager", bind=True, acks_late=True)
def transcribe_kc_manager(self, *, model: str, manager_folder: str | None, limit: int, pipeline_run_id: int) -> dict:
    db = SessionLocal()
    try:
        stats = transcribe_kc_calls(db, model_size=model, manager_folder=manager_folder, limit=int(limit), pipeline_run_id=int(pipeline_run_id))
        return {"manager_folder": manager_folder, **stats}
    finally:
        db.close()


@shared_task(name="jobs.transcribe_kc_finalize", bind=True)
def transcribe_kc_finalize(self, results: list[dict], *, pipeline_run_id: int) -> dict:
    processed = sum(int(r.get("transcribed") or 0) for r in (results or []))
    total_audio_seconds = sum(float(r.get("total_audio_seconds") or 0.0) for r in (results or []))
    total_transcribe_seconds = sum(float(r.get("total_transcribe_seconds") or 0.0) for r in (results or []))
    avg_rtf = (total_transcribe_seconds / total_audio_seconds) if total_audio_seconds > 0 else None

    db = SessionLocal()
    try:
        finish_pipeline_run(
            db,
            pipeline_run_id=int(pipeline_run_id),
            status="SUCCESS",
            finished_at=datetime.now(timezone.utc),
            processed_calls=int(processed),
            duration_seconds=None,
            error_message=None,
            total_audio_seconds=float(total_audio_seconds),
            avg_rtf=float(avg_rtf) if avg_rtf is not None else None,
        )
    finally:
        db.close()

    return {"status": "ok", "pipeline_run_id": int(pipeline_run_id), "processed": int(processed), "avg_rtf": avg_rtf}


@shared_task(name="jobs.transcribe_kc_day", bind=True)
def transcribe_kc_day(
    self,
    *,
    day: str | None = None,
    root: str | None = None,
    model: str = "medium",
    manager_limit: int | None = None,
    limit: int = 100000,
) -> dict:
    """
    Orchestrates KC daily pipeline:
    - scan day folder into DB (idempotent)
    - enqueue parallel transcription per manager folder
    - finish pipeline_run when all manager tasks are done
    """
    root_dir = Path(root or os.getenv("KC_ROOT", r"C:\Audio_share\Contact_center"))
    day_folder = normalize_day_folder(day or datetime.now().strftime("%d%m%Y"))

    lock_file = _lock_path(day_folder)
    with portalocker.Lock(lock_file, timeout=0):
        db = SessionLocal()
        pipeline_run = create_pipeline_run(
            db,
            started_at=datetime.now(timezone.utc),
            status="RUNNING",
            pipeline_code=PIPELINE_CODE,
        )
        db.close()

        db2 = SessionLocal()
        try:
            collect_kc_calls_metadata(db2, root_dir=root_dir, day=day_folder, manager_limit=manager_limit)
        finally:
            db2.close()

        day_dir = root_dir / day_folder
        manager_dirs = sorted([p for p in day_dir.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
        if manager_limit:
            manager_dirs = manager_dirs[: int(manager_limit)]

        header = [transcribe_kc_manager.s(model=model, manager_folder=p.name, limit=int(limit), pipeline_run_id=pipeline_run.id) for p in manager_dirs]
        callback = transcribe_kc_finalize.s(pipeline_run_id=pipeline_run.id)
        chord(header)(callback)
        return {"status": "enqueued", "pipeline_run_id": pipeline_run.id, "day": day_folder, "managers": len(manager_dirs)}

