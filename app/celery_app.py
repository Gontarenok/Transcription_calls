from __future__ import annotations

import os

from celery import Celery
from celery.signals import worker_process_init, worker_ready


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def make_celery() -> Celery:
    broker_url = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    result_backend = os.getenv("CELERY_RESULT_BACKEND", broker_url)

    celery = Celery("transcription_calls", broker=broker_url, backend=result_backend, include=[])
    celery.conf.update(
        task_default_queue=os.getenv("CELERY_DEFAULT_QUEUE", "q.default"),
        task_default_exchange="tasks",
        task_default_routing_key="tasks",
        task_acks_late=True,
        worker_prefetch_multiplier=int(os.getenv("CELERY_PREFETCH", "1")),
        worker_max_tasks_per_child=int(os.getenv("CELERY_MAX_TASKS_PER_CHILD", "20")),
        task_reject_on_worker_lost=True,
        task_track_started=True,
        timezone=os.getenv("CELERY_TIMEZONE", "UTC"),
        enable_utc=_bool_env("CELERY_ENABLE_UTC", True),
        broker_connection_retry_on_startup=True,
    )

    # Named queues (routing by task name prefix).
    celery.conf.task_routes = {
        "jobs.transcribe_*": {"queue": "q.transcribe"},
        "jobs.classify_*": {"queue": "q.classify"},
        "jobs.catalog_*": {"queue": "q.catalog"},
        "jobs.summarize_*": {"queue": "q.summarize"},
    }

    celery.autodiscover_tasks(["jobs"])
    return celery


celery_app = make_celery()


@worker_process_init.connect
def _install_pipeline_shutdown_on_worker_child(**_kwargs: object) -> None:
    """Prefork: в каждом дочернем процессе — SIGTERM/SIGINT завершают RUNNING-пайплайны статусом INTERRUPTED."""
    from jobs.pipeline_lifecycle import install_worker_shutdown_handlers

    install_worker_shutdown_handlers()


@worker_ready.connect
def _install_pipeline_shutdown_on_worker_ready(**_kwargs: object) -> None:
    """Solo/pthreads: один процесс выполнения задач — те же обработчики (идемпотентная установка)."""
    from jobs.pipeline_lifecycle import install_worker_shutdown_handlers

    install_worker_shutdown_handlers()

