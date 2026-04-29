from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.workers.heartbeat import worker_heartbeat_is_fresh

LOGGER = logging.getLogger(__name__)


def start_worker_health_server(
    *,
    worker_name: str,
    session_factory: sessionmaker[Session],
    port: int,
    max_age_seconds: int,
) -> threading.Thread | None:
    if port <= 0:
        return None

    handler_cls = _build_handler(
        worker_name=worker_name,
        session_factory=session_factory,
        max_age_seconds=max_age_seconds,
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    thread = threading.Thread(
        target=server.serve_forever,
        name=f"worker-health-{port}",
        daemon=True,
    )
    thread.start()
    LOGGER.info("Worker health server listening on port %s.", port)
    return thread


def _build_handler(
    *,
    worker_name: str,
    session_factory: sessionmaker[Session],
    max_age_seconds: int,
) -> type[BaseHTTPRequestHandler]:
    class WorkerHealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
            if self.path != "/healthz":
                self.send_response(404)
                self.end_headers()
                return

            healthy = False
            try:
                with session_factory() as session:
                    healthy = worker_heartbeat_is_fresh(
                        session,
                        worker_name=worker_name,
                        max_age_seconds=max_age_seconds,
                    )
            except Exception:
                LOGGER.warning("Worker health check failed.", exc_info=True)

            status_code = 200 if healthy else 503
            payload: dict[str, Any] = {
                "status": "ok" if healthy else "stale",
                "worker_name": worker_name,
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return WorkerHealthHandler
