"""PostgreSQL-backed durable jobs and workers.

The API only enqueues and reads jobs. A separate worker process claims due rows
with ``FOR UPDATE SKIP LOCKED``, executes registered handlers, and records the
result or retry schedule. Queue state therefore survives API and worker
restarts, and multiple workers can share PostgreSQL without a broker.
"""

from __future__ import annotations

import random
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

from sqlalchemy import Engine, delete, select

from .observability import log_event
from .persistence.session import session_scope
from .persistence.tables import JobRow


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _calendar_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter."""

    max_attempts: int
    initial_delay_seconds: float
    maximum_delay_seconds: float

    def delay_seconds(self, failed_attempt: int, random_value: float) -> float:
        ceiling = min(
            self.maximum_delay_seconds,
            self.initial_delay_seconds * (2 ** max(0, failed_attempt - 1)),
        )
        return max(0.0, min(1.0, random_value)) * ceiling


@dataclass(frozen=True)
class RetentionPolicy:
    completed_years: int = 6
    failed_days: int = 90


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    status: JobStatus
    organization_id: str
    payload: dict[str, Any]
    case_id: Optional[str] = None
    request_id: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 1
    created_at: Optional[datetime] = None
    next_attempt_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error_type: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_finished(self) -> bool:
        return self.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}

    def as_dict(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            normalized = _aware(value)
            return normalized.isoformat() if normalized is not None else None

        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "case_id": self.case_id,
            "request_id": self.request_id,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": iso(self.created_at),
            "next_attempt_at": iso(self.next_attempt_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "result": self.result,
            "error_type": self.error_type,
            "error": self.error,
        }


def _job(row: JobRow) -> Job:
    return Job(
        id=row.id,
        kind=row.kind,
        status=JobStatus(row.status),
        organization_id=row.organization_id,
        payload=row.payload,
        case_id=row.case_id,
        request_id=row.request_id,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        created_at=_aware(row.created_at),
        next_attempt_at=_aware(row.next_attempt_at),
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        lease_expires_at=_aware(row.lease_expires_at),
        expires_at=_aware(row.expires_at),
        result=row.result,
        error_type=row.error_type,
        error=row.error,
    )


class JobQueue:
    """Transactional queue operations shared by API and worker processes."""

    def __init__(
        self,
        engine: Engine,
        *,
        retry_policy: RetryPolicy,
        retention_policy: RetentionPolicy,
        clock: Callable[[], datetime] = _utcnow,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self.engine = engine
        self.retry_policy = retry_policy
        self.retention_policy = retention_policy
        self.clock = clock
        self.random_source = random_source

    def submit(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        organization_id: str,
        case_id: Optional[str] = None,
        request_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> Job:
        now = self.clock()
        row = JobRow(
            id=job_id or uuid.uuid4().hex,
            organization_id=organization_id,
            case_id=case_id,
            request_id=request_id,
            kind=kind,
            status=JobStatus.QUEUED.value,
            payload=payload,
            attempts=0,
            max_attempts=self.retry_policy.max_attempts,
            created_at=now,
            next_attempt_at=now,
        )
        with session_scope(self.engine) as session:
            session.add(row)
            session.flush()
            result = _job(row)
        log_event(
            "job.queued",
            case_id=case_id,
            request_id=request_id,
            organization_id=organization_id,
        )
        return result

    def get(self, job_id: str, *, organization_id: str) -> Optional[Job]:
        with session_scope(self.engine) as session:
            row = session.execute(
                select(JobRow).where(
                    JobRow.id == job_id,
                    JobRow.organization_id == organization_id,
                )
            ).scalar_one_or_none()
            return _job(row) if row is not None else None

    def recent(self, *, organization_id: str, limit: int = 50) -> list[Job]:
        with session_scope(self.engine) as session:
            rows = session.execute(
                select(JobRow)
                .where(JobRow.organization_id == organization_id)
                .order_by(JobRow.created_at.desc())
                .limit(limit)
            ).scalars()
            return [_job(row) for row in rows]

    def claim(self, *, worker_id: str, lease_seconds: float) -> Optional[Job]:
        now = self.clock()
        with session_scope(self.engine) as session:
            expired = session.execute(
                select(JobRow)
                .where(
                    JobRow.status == JobStatus.RUNNING.value,
                    JobRow.lease_expires_at <= now,
                )
                .with_for_update(skip_locked=True)
            ).scalars()
            for row in expired:
                row.worker_id = None
                row.lease_expires_at = None
                row.error_type = "WorkerLeaseExpired"
                row.error = "The worker stopped renewing its lease before completing the job."
                if row.attempts >= row.max_attempts:
                    row.status = JobStatus.FAILED.value
                    row.finished_at = now
                    row.expires_at = now + timedelta(days=self.retention_policy.failed_days)
                else:
                    delay = self.retry_policy.delay_seconds(row.attempts, self.random_source())
                    row.status = JobStatus.QUEUED.value
                    row.next_attempt_at = now + timedelta(seconds=delay)
            session.flush()

            row = session.execute(
                select(JobRow)
                .where(
                    JobRow.status == JobStatus.QUEUED.value,
                    JobRow.next_attempt_at <= now,
                )
                .order_by(JobRow.next_attempt_at, JobRow.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = JobStatus.RUNNING.value
            row.worker_id = worker_id
            row.attempts += 1
            row.started_at = now
            row.finished_at = None
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            session.flush()
            return _job(row)

    def complete(self, job_id: str, *, worker_id: str, result: dict[str, Any]) -> Job:
        now = self.clock()
        with session_scope(self.engine) as session:
            row = session.execute(
                select(JobRow)
                .where(JobRow.id == job_id, JobRow.worker_id == worker_id)
                .with_for_update()
            ).scalar_one()
            row.status = JobStatus.SUCCEEDED.value
            row.result = result
            row.error_type = None
            row.error = None
            row.finished_at = now
            row.lease_expires_at = None
            row.expires_at = _calendar_years(now, self.retention_policy.completed_years)
            session.flush()
            completed = _job(row)
        log_event(
            "job.succeeded",
            case_id=completed.case_id,
            request_id=completed.request_id,
            organization_id=completed.organization_id,
        )
        return completed

    def renew_lease(self, job_id: str, *, worker_id: str, lease_seconds: float) -> bool:
        """Extend a running job lease only while this worker still owns it."""
        now = self.clock()
        with session_scope(self.engine) as session:
            row = session.execute(
                select(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.status == JobStatus.RUNNING.value,
                    JobRow.worker_id == worker_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return False
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return True

    def fail(self, job_id: str, *, worker_id: str, exc: Exception) -> Job:
        now = self.clock()
        with session_scope(self.engine) as session:
            row = session.execute(
                select(JobRow)
                .where(JobRow.id == job_id, JobRow.worker_id == worker_id)
                .with_for_update()
            ).scalar_one()
            row.error_type = type(exc).__name__
            row.error = str(exc)
            row.lease_expires_at = None
            row.worker_id = None
            if row.attempts < row.max_attempts:
                delay = self.retry_policy.delay_seconds(row.attempts, self.random_source())
                row.status = JobStatus.QUEUED.value
                row.next_attempt_at = now + timedelta(seconds=delay)
                row.finished_at = None
                row.expires_at = None
                event = "job.retry_scheduled"
            else:
                row.status = JobStatus.FAILED.value
                row.finished_at = now
                row.expires_at = now + timedelta(days=self.retention_policy.failed_days)
                event = "job.failed"
            session.flush()
            failed = _job(row)
        log_event(
            event,
            case_id=failed.case_id,
            request_id=failed.request_id,
            organization_id=failed.organization_id,
            error_type=type(exc).__name__,
            error=str(exc),
            attempts=failed.attempts,
        )
        return failed

    def purge_expired(self) -> int:
        now = self.clock()
        with session_scope(self.engine) as session:
            result = session.execute(
                delete(JobRow).where(
                    JobRow.status.in_([JobStatus.SUCCEEDED.value, JobStatus.FAILED.value]),
                    JobRow.expires_at <= now,
                )
            )
            return int(result.rowcount or 0)


JobHandler = Callable[[Job], dict[str, Any]]


class JobWorker:
    """Claims and executes durable jobs; suitable for a separate worker process."""

    def __init__(
        self,
        queue: JobQueue,
        handlers: dict[str, JobHandler],
        *,
        lease_seconds: float,
        heartbeat_seconds: Optional[float] = None,
        worker_id: Optional[str] = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self.queue = queue
        self.handlers = handlers
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds or max(1.0, lease_seconds / 3.0)
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("The heartbeat interval must be shorter than the job lease.")
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex}"
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()

    def run_once(self) -> bool:
        job = self.queue.claim(worker_id=self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(job.id, heartbeat_stop),
            name=f"{self.worker_id}-heartbeat",
            daemon=True,
        )
        heartbeat.start()
        try:
            handler = self.handlers[job.kind]
            result = handler(job)
        except Exception as exc:  # noqa: BLE001 - failure is persisted and retried
            self.queue.fail(job.id, worker_id=self.worker_id, exc=exc)
        else:
            self.queue.complete(job.id, worker_id=self.worker_id, result=result)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=min(1.0, self.heartbeat_seconds))
        return True

    def _heartbeat(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            if not self.queue.renew_lease(
                job_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            ):
                return

    def run_forever(
        self,
        *,
        maintenance: Optional[Callable[[], None]] = None,
        maintenance_interval_seconds: float = 3600.0,
    ) -> None:
        next_maintenance = 0.0
        while not self._stop.is_set():
            current = time.monotonic()
            if maintenance is not None and current >= next_maintenance:
                maintenance()
                next_maintenance = current + maintenance_interval_seconds
            if not self.run_once():
                self._stop.wait(self.poll_seconds)

    def shutdown(self) -> None:
        self._stop.set()


__all__ = [
    "Job",
    "JobHandler",
    "JobQueue",
    "JobStatus",
    "JobWorker",
    "RetentionPolicy",
    "RetryPolicy",
]
