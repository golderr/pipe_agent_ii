from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.models import WorkerHeartbeat

LOGGER = logging.getLogger(__name__)
_PROCESS_STARTED_AT = datetime.now(UTC)


def write_worker_heartbeat(
    session: Session,
    *,
    worker_name: str,
    active_job_id: uuid.UUID | None = None,
    active_job_started_at: datetime | None = None,
    metadata: dict | None = None,
    now: datetime | None = None,
    process_started_at: datetime | None = None,
) -> None:
    heartbeat_at = now or datetime.now(UTC)
    started_at = process_started_at or _PROCESS_STARTED_AT
    statement = insert(WorkerHeartbeat).values(
        worker_name=worker_name,
        last_heartbeat_at=heartbeat_at,
        process_started_at=started_at,
        active_job_id=active_job_id,
        active_job_started_at=active_job_started_at,
        heartbeat_metadata=metadata,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_name],
            set_={
                "last_heartbeat_at": heartbeat_at,
                "process_started_at": started_at,
                "active_job_id": active_job_id,
                "active_job_started_at": active_job_started_at,
                "metadata": metadata,
            },
        )
    )


def worker_heartbeat_is_fresh(
    session: Session,
    *,
    worker_name: str,
    max_age_seconds: int,
    now: datetime | None = None,
) -> bool:
    heartbeat = session.get(WorkerHeartbeat, worker_name)
    if heartbeat is None:
        return False
    heartbeat_at = heartbeat.last_heartbeat_at
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
    return heartbeat_at >= (now or datetime.now(UTC)) - timedelta(seconds=max_age_seconds)


def start_heartbeat_thread(
    *,
    worker_name: str,
    session_factory: sessionmaker[Session],
    interval_seconds: int,
    metadata_factory: Callable[[], dict | None] | None = None,
) -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                with session_factory() as session:
                    write_worker_heartbeat(
                        session,
                        worker_name=worker_name,
                        metadata=metadata_factory() if metadata_factory else None,
                    )
                    session.commit()
            except Exception:
                LOGGER.warning("Worker heartbeat write failed.", exc_info=True)
            time.sleep(interval_seconds)

    thread = threading.Thread(
        target=loop,
        name=f"heartbeat-{worker_name}",
        daemon=True,
    )
    thread.start()
    return thread
