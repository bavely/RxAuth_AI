"""HTTP surface for the case workflow (roadmap Stage 2).

Six endpoints, deliberately: create a case, upload its documents, start a run,
poll the run, read the result, record a reviewer decision. That is the whole
flow from §5, and nothing here decides anything the CLI does not already
decide — the API is a way to reach the workflow, not a second implementation
of it.

**Sync, not async.** Every dependency underneath is synchronous and
CPU-bound — scikit-learn, regex extraction, pypdf, OpenCV. Declaring the
handlers `async def` would run that work on the event loop and block every
other request; FastAPI runs plain `def` handlers on a threadpool, which is the
correct place for it. The one genuinely long operation, a case run, goes to the
job runner instead of being held open.

**No authentication yet, and it is refused rather than defaulted.** Auth and
RBAC are Stage 3. Until they exist, `require_auth_configured` blocks startup in
`staging` and `production`, so an unauthenticated service cannot be deployed by
accident with PHI behind it. Locally it runs, and says so.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from .case_assembly import CaseManifest, build_output, load_classifier
from .config import Settings, get_settings
from .feedback import ReviewerAction, decision_from_evaluation
from .jobs import JobRunner
from .models import CriterionResult
from .observability import RunContext, configure_logging, log_event
from .persistence import (
    DatabaseNotConfiguredError,
    engine_for,
    load_case_run,
    load_reviewer_decisions,
    recent_case_runs,
    save_case_run,
    save_reviewer_decision,
    session_scope,
)
from .policy_retrieval import build_index
from .storage import build_object_store, document_key
from .workflow import run_case_workflow

API_VERSION = "v1"


class CaseCreate(BaseModel):
    """The declared facts a packet must state (README section 3)."""

    case_id: str = Field(min_length=1, max_length=128)
    patient_synthetic_id: str = Field(min_length=1)
    payer: str = Field(min_length=1)
    medication: str = Field(min_length=1)
    indication: str = Field(min_length=1)
    pa_required: bool = Field(
        description="Synthetic benefit trigger or explicit user input. Never inferred."
    )
    plan: Optional[str] = None
    policy_id: Optional[str] = None
    request_date: Optional[str] = None


class ReviewerDecisionCreate(BaseModel):
    criterion_id: str
    reviewer_id: str
    action: ReviewerAction
    corrected_result: Optional[CriterionResult] = None
    corrected_evidence_ids: Optional[list[str]] = None
    note: Optional[str] = None


def require_auth_configured(settings: Settings) -> None:
    """Refuse to serve an unauthenticated API outside a developer machine.

    Authentication is Stage 3. A service with no auth is fine on a laptop with
    synthetic data and is a breach waiting to happen anywhere else, so the
    boundary is enforced at startup rather than trusted to a deployment note.
    """
    if settings.environment in {"staging", "production"}:
        raise RuntimeError(
            f"Refusing to start in environment={settings.environment}: this API has no "
            "authentication or access control yet (roadmap Stage 3). Deploying it in front of "
            "real data would violate README section 19."
        )


def _case_dir(settings: Settings, case_id: str) -> Path:
    return Path(settings.artifacts_dir) / "cases" / case_id


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build the application around one settings object.

    Every dependency below resolves from `active` rather than from the
    process-wide `get_settings()`. An app built with explicit settings has to
    honour them everywhere, or `create_app(settings)` is a lie that only shows
    up as a 503 in the one handler that reached past it.
    """
    active = settings or get_settings()
    require_auth_configured(active)
    configure_logging(active)

    engine = engine_for(active) if active.database_url else None
    runner = JobRunner(workers=active.job_workers, retention=active.job_retention)

    def app_settings() -> Settings:
        return active

    def require_database(settings: Settings = Depends(app_settings)) -> Settings:
        if not settings.database_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "The API needs RXAUTH_DATABASE_URL. See docker-compose.yml for a local one."
                ),
            )
        return settings

    def app_runner() -> JobRunner:
        return runner

    def transaction():
        return session_scope(engine)

    app = FastAPI(
        title="RxAuth AI",
        version=API_VERSION,
        summary="Evidence-grounded prior-authorization intelligence.",
        description=(
            "Administrative decision support. This service prepares a prior-authorization "
            "case for a human reviewer; it does not decide, approve, deny, or submit one."
        ),
    )
    app.state.settings = active
    app.state.job_runner = runner
    app.state.engine = engine

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": API_VERSION,
            "environment": active.environment,
            "database": bool(active.database_url),
            "storage": "s3" if not active.storage_is_local else "local",
        }

    @app.post("/cases", status_code=status.HTTP_201_CREATED)
    def create_case(payload: CaseCreate) -> dict[str, Any]:
        """Write the case manifest. Documents are uploaded separately."""
        manifest = CaseManifest.model_validate(payload.model_dump())
        directory = _case_dir(active, manifest.case_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "case.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8", newline="\n"
        )
        log_event("case.created", case_id=manifest.case_id)
        return {"case_id": manifest.case_id, "documents": 0}

    @app.post("/cases/{case_id}/documents", status_code=status.HTTP_201_CREATED)
    def upload_document(case_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        """Store one document, in object storage and on the working directory.

        Both, on purpose: object storage is the record of what was submitted,
        and the pipeline reads files from a directory. The stored copy is the
        one that outlives the container.
        """
        directory = _case_dir(active, case_id)
        if not (directory / "case.json").is_file():
            raise HTTPException(status_code=404, detail=f"No case {case_id!r}. Create it first.")

        filename = Path(file.filename or "document.txt").name
        target = directory / filename
        with target.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)

        store = build_object_store(active)
        key = document_key(case_id, target.stem, filename, prefix=active.s3_prefix)
        with target.open("rb") as handle:
            stored = store.put(key, handle)

        log_event("document.stored", case_id=case_id)
        return {
            "case_id": case_id,
            "filename": filename,
            "storage_key": stored.key,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }

    @app.post("/cases/{case_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    def start_run(
        case_id: str,
        settings: Settings = Depends(require_database),
        runner: JobRunner = Depends(app_runner),
    ) -> dict[str, Any]:
        """Accept a run and return a job to poll.

        202, not 200: the work has been accepted, not done. A case run is
        seconds at best and minutes on a scanned packet.
        """
        directory = _case_dir(settings, case_id)
        if not (directory / "case.json").is_file():
            raise HTTPException(status_code=404, detail=f"No case {case_id!r}.")

        context = RunContext(case_id=case_id)

        def work() -> dict[str, Any]:
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
            with transaction() as session:
                run_id = save_case_run(session, payload=payload, request_id=context.request_id)
            return {"run_id": run_id, "case_id": case_id, "summary": state.report.summary_line()}

        job = runner.submit("case_run", work, case_id=case_id, request_id=context.request_id)
        return job.as_dict()

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str, runner: JobRunner = Depends(app_runner)) -> dict[str, Any]:
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No job {job_id!r}. Jobs are held in memory and do not survive a restart "
                    "(see jobs.py); a finished run is still readable at /runs."
                ),
            )
        return job.as_dict()

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, settings: Settings = Depends(require_database)) -> dict[str, Any]:
        """The full run document — the same bytes written to reports/."""
        with transaction() as session:
            record = load_case_run(session, run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No run {run_id!r}.")
        return record.payload

    @app.get("/cases/{case_id}/runs")
    def list_runs(
        case_id: str, limit: int = 20, settings: Settings = Depends(require_database)
    ) -> dict[str, Any]:
        with transaction() as session:
            records = recent_case_runs(session, case_id=case_id, limit=limit)
        return {
            "case_id": case_id,
            "runs": [
                {
                    "run_id": record.run_id,
                    "created_at": record.created_at,
                    "summary": record.report.summary_line(),
                    "matcher_version": record.report.matcher_version,
                    "groundedness_gate": record.report.groundedness_gate,
                }
                for record in records
            ],
        }

    @app.post("/runs/{run_id}/decisions", status_code=status.HTTP_201_CREATED)
    def record_decision(
        run_id: str,
        payload: ReviewerDecisionCreate,
        settings: Settings = Depends(require_database),
    ) -> dict[str, Any]:
        """Record what a reviewer decided about one criterion (README section 16)."""
        with transaction() as session:
            record = load_case_run(session, run_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"No run {run_id!r}.")
            evaluation = next(
                (item for item in record.evaluations if item.criterion_id == payload.criterion_id),
                None,
            )
            if evaluation is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Run {run_id!r} has no criterion {payload.criterion_id!r}.",
                )
            try:
                decision = decision_from_evaluation(
                    evaluation,
                    reviewer_id=payload.reviewer_id,
                    action=payload.action,
                    corrected_result=payload.corrected_result,
                    corrected_evidence_ids=payload.corrected_evidence_ids,
                    note=payload.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            decision_id = save_reviewer_decision(session, decision, run_id=run_id)

        log_event("reviewer.decision", case_id=record.case_id, criterion_id=payload.criterion_id)
        return {"decision_id": decision_id, "run_id": run_id, "action": decision.action.value}

    @app.get("/cases/{case_id}/decisions")
    def list_decisions(
        case_id: str, settings: Settings = Depends(require_database)
    ) -> dict[str, Any]:
        with transaction() as session:
            decisions = load_reviewer_decisions(session, case_id=case_id)
        return {
            "case_id": case_id,
            "decisions": [decision.model_dump(mode="json") for decision in decisions],
        }

    @app.exception_handler(DatabaseNotConfiguredError)
    def _database_missing(_request: Any, exc: DatabaseNotConfiguredError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"detail": str(exc)})

    return app


def main() -> None:
    """Run the service. Development entry point; production uses a process manager."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - bound inside a container, published deliberately
        port=8000,
        log_config=None,
    )


# Module-level app for `uvicorn rxauth_ai.api:app`. Built lazily by the ASGI
# server rather than at import, so importing this module never starts anything.
def __getattr__(name: str) -> Any:
    if name == "app":
        return create_app()
    raise AttributeError(name)
