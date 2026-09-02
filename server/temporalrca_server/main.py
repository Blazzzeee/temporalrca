from __future__ import annotations

import json
import logging
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import ORJSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text

from . import __version__
from .api import router
from .config import get_settings
from .database import SessionLocal

REQUESTS = Counter("temporalrca_http_requests_total", "HTTP requests", ["method", "path", "status"])
DURATION = Histogram("temporalrca_http_request_duration_seconds", "HTTP request duration", ["method", "path"])


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"timestamp": self.formatTime(record), "level": record.levelname, "logger": record.name, "message": record.getMessage()})


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=get_settings().log_level, handlers=[handler], force=True)


def create_app() -> FastAPI:
    app = FastAPI(title="TemporalRCA Telemetry API", version=__version__, default_response_class=ORJSONResponse)
    app.include_router(router)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            REQUESTS.labels(request.method, request.url.path, "500").inc()
            logging.getLogger("temporalrca.http").exception("request failed", extra={"request_id": request_id})
            raise
        response.headers["x-request-id"] = request_id
        route = request.scope.get("route")
        path_label = getattr(route, "path", request.url.path)
        REQUESTS.labels(request.method, path_label, str(response.status_code)).inc()
        DURATION.labels(request.method, path_label).observe(time.monotonic() - started)
        return response

    @app.get("/health/live", include_in_schema=False)
    async def liveness(): return {"status": "ok", "version": __version__}

    @app.get("/health/ready", include_in_schema=False)
    async def readiness():
        try:
            async with SessionLocal() as session: await session.execute(text("SELECT 1"))
            return {"status": "ready"}
        except Exception:
            return ORJSONResponse({"status": "not_ready"}, status_code=503)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(): return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
    return app


configure_logging()
app = create_app()


def run() -> None:
    configure_logging()
    uvicorn.run("temporalrca_server.main:app", host="0.0.0.0", port=8000, proxy_headers=True)
