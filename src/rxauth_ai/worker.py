"""CLI entry point for the PostgreSQL-backed case worker."""

from __future__ import annotations

from .case_jobs import build_case_job_handler, cleanup_expired_temporary_copies
from .config import get_settings
from .jobs import JobQueue, JobWorker, RetentionPolicy, RetryPolicy
from .observability import configure_logging, log_event
from .persistence import engine_for


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    engine = engine_for(settings)
    queue = JobQueue(
        engine,
        retry_policy=RetryPolicy(
            max_attempts=settings.job_max_attempts,
            initial_delay_seconds=settings.effective_job_retry_initial_seconds,
            maximum_delay_seconds=settings.effective_job_retry_max_seconds,
        ),
        retention_policy=RetentionPolicy(
            completed_years=settings.completed_job_retention_years,
            failed_days=settings.failed_job_retention_days,
        ),
    )

    def maintenance() -> None:
        removed_jobs = queue.purge_expired()
        removed_directories = cleanup_expired_temporary_copies(settings)
        log_event(
            "worker.maintenance",
            counts={
                "jobs_purged": removed_jobs,
                "temporary_copies_purged": removed_directories,
            },
        )

    log_event("worker.started")
    worker = JobWorker(
        queue,
        {"case_run": build_case_job_handler(settings, engine)},
        lease_seconds=settings.job_lease_seconds,
        heartbeat_seconds=settings.job_heartbeat_seconds,
        poll_seconds=settings.job_poll_seconds,
    )
    try:
        worker.run_forever(maintenance=maintenance)
    except KeyboardInterrupt:
        worker.shutdown()


if __name__ == "__main__":
    main()
