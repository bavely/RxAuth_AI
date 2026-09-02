"""Durable case-run handler shared by API tests and the worker process."""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import Engine

from .case_assembly import CaseManifest, build_output, load_classifier
from .config import Settings
from .jobs import Job
from .observability import RunContext
from .persistence import (
    list_uploaded_documents,
    load_case_record,
    load_case_run,
    save_case_run,
    session_scope,
)
from .policy_retrieval import build_index
from .storage import build_object_store
from .workflow import run_case_workflow


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _case_dir(settings: Settings, organization_id: str, case_id: str) -> Path:
    root = (Path(settings.artifacts_dir) / "cases" / organization_id).resolve()
    directory = (root / case_id).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Case path escapes its organization root.")
    return directory


def cleanup_expired_temporary_copies(settings: Settings, *, now: datetime | None = None) -> int:
    """Delete tenant working directories older than the 72-hour contract.

    Original bytes remain in object storage and metadata remains in PostgreSQL,
    so a later retry reconstructs the directory with integrity checks.
    """
    root = (Path(settings.artifacts_dir) / "cases").resolve()
    if not root.is_dir():
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        hours=settings.temporary_copy_retention_hours
    )
    removed = 0
    for organization in root.iterdir():
        if not organization.is_dir() or organization.is_symlink():
            continue
        for case_directory in organization.iterdir():
            if not case_directory.is_dir() or case_directory.is_symlink():
                continue
            resolved = case_directory.resolve()
            if not resolved.is_relative_to(root):
                continue
            modified = datetime.fromtimestamp(case_directory.stat().st_mtime, timezone.utc)
            if modified <= cutoff:
                shutil.rmtree(case_directory)
                removed += 1
        try:
            organization.rmdir()
        except OSError:
            pass
    return removed


def build_case_job_handler(settings: Settings, engine: Engine) -> Callable[[Job], dict]:
    store = build_object_store(settings)

    def run(job: Job) -> dict:
        if not job.case_id:
            raise ValueError("A case_run job requires case_id.")

        # A worker may finish the durable run and crash before marking the job
        # succeeded. The retry reuses the job id and returns the existing run.
        with session_scope(engine) as session:
            existing = load_case_run(session, run_id=job.id, organization_id=job.organization_id)
            if existing is not None:
                return {
                    "run_id": existing.run_id,
                    "case_id": existing.case_id,
                    "summary": existing.report.summary_line(),
                }
            case = load_case_record(
                session,
                organization_id=job.organization_id,
                case_id=job.case_id,
            )
            documents = list_uploaded_documents(
                session,
                organization_id=job.organization_id,
                case_id=job.case_id,
            )
        if case is None:
            raise FileNotFoundError(
                f"No case {job.case_id!r} in organization {job.organization_id!r}."
            )
        if not documents:
            raise ValueError(f"Case {job.case_id!r} has no uploaded documents.")

        directory = _case_dir(settings, job.organization_id, job.case_id)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = CaseManifest.model_validate(case.manifest)
        (directory / "case.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8", newline="\n"
        )

        storage_keys: dict[str, str] = {}
        for document in documents:
            destination = directory / document.filename
            if not destination.is_file() or _digest(destination) != document.sha256:
                temporary = directory / f".{document.id}.download"
                temporary.unlink(missing_ok=True)
                try:
                    store.get(document.storage_key, temporary)
                    if _digest(temporary) != document.sha256:
                        raise ValueError(
                            f"Stored document {document.id!r} failed its SHA-256 integrity check."
                        )
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            storage_keys[destination.stem] = document.storage_key

        classifier = load_classifier(settings.classifier_path)
        result = run_case_workflow(
            directory,
            classifier=classifier,
            index=build_index(settings.policy_dir),
            confidence_threshold=settings.extraction_confidence_threshold,
            criteria_confidence_threshold=settings.criteria_confidence_threshold,
        )
        if result.error is not None:
            raise result.error

        state = result.state
        assert state.report is not None
        assert state.assembled is not None
        assert state.resolved is not None
        payload = build_output(
            state.report,
            state.assembled,
            state.resolved,
            directory,
            workflow_records=result.record_dicts(),
            checklist=state.checklist,
            draft_groundedness=state.draft_groundedness,
        )
        context = RunContext(request_id=job.request_id or job.id, case_id=job.case_id)
        with session_scope(engine) as session:
            run_id = save_case_run(
                session,
                payload=payload,
                request_id=context.request_id,
                organization_id=job.organization_id,
                storage_keys=storage_keys,
                run_id=job.id,
            )
        return {
            "run_id": run_id,
            "case_id": job.case_id,
            "summary": state.report.summary_line(),
        }

    return run
