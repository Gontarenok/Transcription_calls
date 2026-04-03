from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Generator

from db.base import SessionLocal
from db.crud import create_pipeline_run, finish_pipeline_run


@dataclass(frozen=True)
class JobContext:
    pipeline_run_id: int
    pipeline_code: str


@contextmanager
def pipeline_run_context(*, pipeline_code: str) -> Generator[JobContext, None, None]:
    """
    Helper to create/finish pipeline_runs around a Celery job.
    Keeps DB writes in the same process but does not require a shared Session across job code.
    """
    started_at = datetime.now(timezone.utc)
    t0 = time.time()
    db = SessionLocal()
    run = create_pipeline_run(db, started_at=started_at, status="RUNNING", pipeline_code=pipeline_code)
    db.close()
    processed_calls = 0
    try:
        yield JobContext(pipeline_run_id=run.id, pipeline_code=pipeline_code)
        status = "SUCCESS"
        error = None
    except Exception as exc:
        status = "FAILED"
        error = str(exc)
        raise
    finally:
        duration = int(time.time() - t0)
        db2 = SessionLocal()
        finish_pipeline_run(
            db2,
            pipeline_run_id=run.id,
            status=status,
            finished_at=datetime.now(timezone.utc),
            processed_calls=int(processed_calls),
            duration_seconds=duration,
            error_message=error,
        )
        db2.close()


def run_with_pipeline(
    *,
    pipeline_code: str,
    fn: Callable[[JobContext], int],
) -> int:
    """
    Small wrapper to standardize pipeline_runs creation and return processed count.
    """
    with pipeline_run_context(pipeline_code=pipeline_code) as ctx:
        processed = int(fn(ctx) or 0)
        return processed

