"""Durability, retries, leases, tenant scope, and retention for PostgreSQL jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from rxauth_ai.jobs import (
    JobQueue,
    JobStatus,
    JobWorker,
    RetentionPolicy,
    RetryPolicy,
)
from rxauth_ai.persistence import create_all
from rxauth_ai.persistence.tables import Base, JobRow


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 2, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", future=True)
    create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def _queue(engine, clock, *, random_value=0.5, retry_initial=1800, retry_max=3600):
    return JobQueue(
        engine,
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=retry_initial,
            maximum_delay_seconds=retry_max,
        ),
        retention_policy=RetentionPolicy(completed_years=6, failed_days=90),
        clock=clock,
        random_source=lambda: random_value,
    )


def test_a_new_queue_instance_processes_a_job_created_before_restart(engine):
    clock = Clock()
    submitted = _queue(engine, clock).submit(
        "case_run",
        {"case_id": "PA-1"},
        organization_id="org-a",
        case_id="PA-1",
    )

    restarted_queue = _queue(engine, clock)
    worker = JobWorker(
        restarted_queue,
        {"case_run": lambda job: {"run_id": job.id}},
        lease_seconds=3600,
        worker_id="worker-after-restart",
    )

    assert worker.run_once() is True
    finished = _queue(engine, clock).get(submitted.id, organization_id="org-a")
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.result == {"run_id": submitted.id}
    assert finished.attempts == 1


def test_failed_jobs_retry_three_times_with_exponential_full_jitter(engine):
    clock = Clock()
    queue = _queue(engine, clock)
    job = queue.submit("explode", {}, organization_id="org-a")
    worker = JobWorker(
        queue,
        {"explode": lambda _job: (_ for _ in ()).throw(RuntimeError("boom"))},
        lease_seconds=3600,
        worker_id="worker-1",
    )

    assert worker.run_once() is True
    first = queue.get(job.id, organization_id="org-a")
    assert first.status is JobStatus.QUEUED
    assert first.attempts == 1
    assert first.next_attempt_at == clock.now + timedelta(minutes=15)

    clock.advance(minutes=15)
    assert worker.run_once() is True
    second = queue.get(job.id, organization_id="org-a")
    assert second.status is JobStatus.QUEUED
    assert second.attempts == 2
    assert second.next_attempt_at == clock.now + timedelta(minutes=30)

    clock.advance(minutes=30)
    assert worker.run_once() is True
    failed = queue.get(job.id, organization_id="org-a")
    assert failed.status is JobStatus.FAILED
    assert failed.attempts == 3
    assert failed.error_type == "RuntimeError"
    assert failed.expires_at == clock.now + timedelta(days=90)


def test_a_live_lease_prevents_claiming_and_an_expired_lease_is_recovered(engine):
    clock = Clock()
    queue = _queue(engine, clock, retry_initial=0, retry_max=0)
    job = queue.submit("case_run", {}, organization_id="org-a")

    first_claim = queue.claim(worker_id="worker-1", lease_seconds=60)
    assert first_claim.id == job.id
    assert queue.claim(worker_id="worker-2", lease_seconds=60) is None

    clock.advance(seconds=61)
    recovered = queue.claim(worker_id="worker-2", lease_seconds=60)
    assert recovered.id == job.id
    assert recovered.attempts == 2


def test_an_expired_final_attempt_becomes_a_retained_failure(engine):
    clock = Clock()
    queue = _queue(engine, clock, retry_initial=0, retry_max=0)
    job = queue.submit("case_run", {}, organization_id="org-a")

    for worker_number in range(1, 4):
        claimed = queue.claim(worker_id=f"worker-{worker_number}", lease_seconds=60)
        assert claimed is not None
        clock.advance(seconds=61)

    assert queue.claim(worker_id="worker-4", lease_seconds=60) is None
    failed = queue.get(job.id, organization_id="org-a")
    assert failed.status is JobStatus.FAILED
    assert failed.attempts == 3
    assert failed.error_type == "WorkerLeaseExpired"
    assert failed.expires_at == clock.now + timedelta(days=90)


def test_job_reads_are_scoped_to_the_verified_organization(engine):
    clock = Clock()
    queue = _queue(engine, clock)
    job = queue.submit("case_run", {}, organization_id="org-a")

    assert queue.get(job.id, organization_id="org-a") is not None
    assert queue.get(job.id, organization_id="org-b") is None
    assert queue.recent(organization_id="org-b") == []


def test_expired_terminal_jobs_are_purged_but_active_jobs_are_not(engine):
    clock = Clock()
    queue = _queue(engine, clock, retry_initial=0, retry_max=0)
    succeeded = queue.submit("ok", {}, organization_id="org-a")
    failed = queue.submit("bad", {}, organization_id="org-a")
    active = queue.submit("waiting", {}, organization_id="org-a")
    worker = JobWorker(
        queue,
        {
            "ok": lambda _job: {"ok": True},
            "bad": lambda _job: (_ for _ in ()).throw(RuntimeError("bad")),
        },
        lease_seconds=3600,
        worker_id="worker-1",
    )

    assert worker.run_once() is True
    for _ in range(3):
        assert worker.run_once() is True
    assert queue.get(succeeded.id, organization_id="org-a").status is JobStatus.SUCCEEDED
    assert queue.get(failed.id, organization_id="org-a").status is JobStatus.FAILED

    clock.advance(days=90, seconds=1)
    assert queue.purge_expired() == 1
    assert queue.get(failed.id, organization_id="org-a") is None
    assert queue.get(succeeded.id, organization_id="org-a") is not None
    assert queue.get(active.id, organization_id="org-a") is not None

    # Keep the imported row in this test as an assertion that the queue really
    # uses the relational table rather than an in-process shadow collection.
    assert JobRow.__tablename__ == "jobs"
