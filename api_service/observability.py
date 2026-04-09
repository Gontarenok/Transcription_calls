from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api_service.config import settings

_LOG_JSON = settings.log_json


class JsonFormatter(logging.Formatter):
    """Структурированные лог-строки (одна JSON-запись на событие)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    if _LOG_JSON:
        root = logging.getLogger()
        root.handlers.clear()
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    else:
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )


def _log_request(
    *,
    request: Request,
    status_code: int | None,
    duration_ms: float,
    request_id: str,
    error: str | None = None,
) -> None:
    log = logging.getLogger("api.request")
    if _LOG_JSON:
        payload = {
            "event": "http_request",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "duration_ms": round(duration_ms, 3),
        }
        if status_code is not None:
            payload["status_code"] = status_code
        if error:
            payload["error"] = error
        log.info(json.dumps(payload, ensure_ascii=False))
    else:
        log.info(
            "%s %s %s %.2fms",
            request.method,
            request.url.path,
            status_code if status_code is not None else "error",
            duration_ms,
        )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Trace id: заголовок X-Request-ID (или новый UUID), ответ с тем же id."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = (request.headers.get("x-request-id") or request.headers.get("X-Request-ID") or "").strip()
        if not rid:
            rid = str(uuid.uuid4())
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            _log_request(request=request, status_code=None, duration_ms=duration_ms, request_id=rid, error=str(exc))
            logging.getLogger("api.request").exception("unhandled")
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid
        _log_request(request=request, status_code=response.status_code, duration_ms=duration_ms, request_id=rid)
        return response


def instrument_prometheus(app: Any) -> None:
    if not settings.prometheus_enabled:
        return
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except ImportError:
        logging.getLogger(__name__).warning("prometheus_fastapi_instrumentator not installed; skip /metrics")


def maybe_setup_opentelemetry(app: Any) -> None:
    if not settings.otel_enabled:
        return
    endpoint = (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    if not endpoint:
        logging.getLogger(__name__).warning("OTEL enabled but OTEL_EXPORTER_OTLP_ENDPOINT is empty; skip OTEL")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger(__name__).warning("OpenTelemetry packages not installed; skip APM")
        return

    insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "1").strip().lower() in {"1", "true", "yes", "on"}
    service_name = os.getenv("OTEL_SERVICE_NAME", "transcription-calls-api")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=insecure))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
