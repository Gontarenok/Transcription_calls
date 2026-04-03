from __future__ import annotations

import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

_registry_lock = threading.Lock()
# run_id -> (monotonic_t0, get_processed)
_registry: dict[int, tuple[float, Callable[[], int]]] = {}
_handlers_installed = False
_install_lock = threading.Lock()


def count_calls_linked_to_pipeline(pipeline_run_id: int) -> int:
    """Число звонков, у которых в БД выставлен pipeline_run_id (оценка прогресса при прерывании)."""
    from sqlalchemy import func, select

    from db.base import SessionLocal
    from db.models import Call

    session = SessionLocal()
    try:
        return int(session.scalar(select(func.count()).select_from(Call).where(Call.pipeline_run_id == pipeline_run_id)) or 0)
    finally:
        session.close()


def register_active_pipeline(pipeline_run_id: int, get_processed: Callable[[], int]) -> None:
    """Помечает пайплайн как выполняющийся в этом воркер-процессе (для прерывания по SIGTERM/SIGINT)."""
    with _registry_lock:
        _registry[pipeline_run_id] = (time.monotonic(), get_processed)


def unregister_active_pipeline(pipeline_run_id: int) -> None:
    with _registry_lock:
        _registry.pop(pipeline_run_id, None)


def finalize_interrupted_pipelines(reason: str) -> None:
    """Закрывает все активные в этом процессе пайплайны статусом INTERRUPTED (вызывается из обработчика сигнала)."""
    from db.base import SessionLocal
    from db.crud import finish_pipeline_run

    with _registry_lock:
        snapshot = list(_registry.items())
        _registry.clear()

    if not snapshot:
        return

    finished_at = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
        for run_id, (t0, get_processed) in snapshot:
            try:
                n = int(get_processed())
            except Exception:
                n = 0
            duration = max(0, int(time.monotonic() - t0))
            finish_pipeline_run(
                session,
                pipeline_run_id=run_id,
                status="INTERRUPTED",
                finished_at=finished_at,
                processed_calls=n,
                duration_seconds=duration,
                error_message=reason,
            )
    finally:
        session.close()


def install_worker_shutdown_handlers() -> None:
    """
    Регистрирует обработчики на SIGTERM/SIGINT в текущем процессе (один раз на процесс).
    Вызывать из дочернего процесса воркера Celery (prefork), см. app/celery_app.py.
    """
    global _handlers_installed
    with _install_lock:
        if _handlers_installed:
            return
        _handlers_installed = True

    reason = (
        "Пайплайн прерван вручную или остановкой воркера (SIGTERM/SIGINT). "
        "Указаны время работы и число обработанных звонков на момент прерывания."
    )

    def _handler(signum: int, frame: object | None) -> None:  # noqa: ARG001
        try:
            finalize_interrupted_pipelines(reason)
        finally:
            signal.signal(signum, signal.SIG_DFL)
            if hasattr(signal, "raise_signal"):
                signal.raise_signal(signum)
            else:  # pragma: no cover
                import os

                os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Windows / ограниченный контекст потока — тихо пропускаем
            pass
