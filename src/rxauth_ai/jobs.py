"""Background case runs on a thread pool (roadmap Stage 2).

A case run is seconds — ingest, classify, extract, retrieve, match, draft — and
on a scanned packet it is longer, because OCR is slow. That is far too long to
hold an HTTP request open, so `POST /cases/{id}/runs` accepts the work and
returns a job to poll.

**Why threads rather than a queue.** The work is CPU-and-IO-bound Python in one
process, the API is sync, and a single-node deployment is the whole of Track A.
A real broker (Celery, RQ, SQS) buys durability across restarts and horizontal
workers, and costs a broker to run and monitor. Neither is needed until there
is more than one node — and this module's surface is small enough that swapping
the executor for a broker client is a contained change.

**What this deliberately does not survive.** A process restart loses queued and
running jobs, and the job table is in memory. That is stated here rather than
discovered in an incident: a durable queue is the fix, and it is a Stage 6
concern, not a thing to pretend about now. Runs are persisted to the database
as they finish, so a *completed* run survives; an interrupted one does not.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from .config import Settings, get_settings
from .observability import log_event


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Job:
    """One unit of background work and everything a poller needs."""

    id: str
    kind: str
    status: JobStatus = JobStatus.QUEUED
    case_id: Optional[str] = None
    request_id: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error_type: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_finished(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "case_id": self.case_id,
            "request_id": self.request_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error_type": self.error_type,
            "error": self.error,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobRunner:
    """A bounded thread pool with a bounded, queryable job table."""

    def __init__(self, *, workers: int = 2, retention: int = 200) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rxauth-job")
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._retention = retention
        self.workers = workers

    def submit(
        self,
        kind: str,
        work: Callable[[], dict[str, Any]],
        *,
        case_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Job:
        """Queue work and return its job immediately."""
        job = Job(id=uuid.uuid4().hex, kind=kind, case_id=case_id, request_id=request_id)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_finished()
        log_event("job.queued", case_id=case_id, request_id=request_id)
        self._executor.submit(self._run, job, work)
        return job

    def _run(self, job: Job, work: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job.status = JobStatus.RUNNING
            job.started_at = _now()
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - recorded on the job, not swallowed
            with self._lock:
                job.status = JobStatus.FAILED
                job.finished_at = _now()
                job.error_type = type(exc).__name__
                # The message may name a file or a policy, never a quoted span:
                # every raise site in the pipeline reports identifiers.
                job.error = str(exc)
                self._evict_finished()
            log_event(
                "job.failed",
                case_id=job.case_id,
                request_id=job.request_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return
        with self._lock:
            job.status = JobStatus.SUCCEEDED
            job.finished_at = _now()
            job.result = result
            self._evict_finished()
        log_event("job.succeeded", case_id=job.case_id, request_id=job.request_id)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 50) -> list[Job]:
        with self._lock:
            return list(reversed(list(self._jobs.values())))[:limit]

    def _evict_finished(self) -> None:
        """Drop the oldest finished jobs once the table is over its bound.

        Only finished ones: evicting a running job would make a poller see a
        404 for work that is still happening, which is worse than forgetting a
        result nobody collected.

        Called on submission *and* on completion. Submission alone is not
        enough — a burst that queues faster than it drains finds nothing
        evictable and grows the table past its bound, which is precisely the
        situation the bound exists for.
        """
        while len(self._jobs) > self._retention:
            for job_id, job in self._jobs.items():
                if job.is_finished:
                    del self._jobs[job_id]
                    break
            else:
                return

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until queued work drains. For tests and for shutdown."""
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._executor = ThreadPoolExecutor(
            max_workers=self.workers, thread_name_prefix="rxauth-job"
        )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)


_runner: Optional[JobRunner] = None
_runner_lock = threading.Lock()


def get_job_runner(settings: Optional[Settings] = None) -> JobRunner:
    """The process-wide runner, created once."""
    global _runner
    with _runner_lock:
        if _runner is None:
            active = settings or get_settings()
            _runner = JobRunner(workers=active.job_workers, retention=active.job_retention)
        return _runner


def reset_job_runner() -> None:
    """Drop the process-wide runner. For tests."""
    global _runner
    with _runner_lock:
        if _runner is not None:
            _runner.shutdown()
        _runner = None


__all__ = [
    "Future",
    "Job",
    "JobRunner",
    "JobStatus",
    "get_job_runner",
    "reset_job_runner",
]
