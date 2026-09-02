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

**Authentication fails closed.** Staging and production require an OIDC issuer,
audience, and JWKS endpoint. Every workflow endpoint resolves a verified
principal, checks its role, and carries its organization into storage and
database lookups. Local development uses one explicit synthetic principal.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Security, UploadFile, status
from fastapi import Path as ApiPath
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import (
    ROLE_CASE_READ,
    ROLE_CASE_WRITE,
    ROLE_REVIEW,
    SAFE_ID_PATTERN,
    AuthenticationError,
    Authenticator,
    Principal,
    build_authenticator,
)
from .case_assembly import CaseManifest
from .case_jobs import build_case_job_handler
from .config import Settings, get_settings
from .feedback import ReviewerAction, decision_from_evaluation
from .jobs import JobQueue, JobWorker, RetentionPolicy, RetryPolicy
from .models import CriterionResult
from .observability import RunContext, configure_logging, log_event
from .persistence import (
    DatabaseNotConfiguredError,
    case_upload_usage,
    create_case_record,
    engine_for,
    list_uploaded_documents,
    load_case_record,
    load_case_run,
    load_reviewer_decisions,
    recent_case_runs,
    save_reviewer_decision,
    save_uploaded_document,
    session_scope,
)
from .storage import build_object_store, document_key
from .uploads import (
    UploadConflictError,
    UploadTooLargeError,
    UploadValidationError,
    stage_upload,
)

API_VERSION = "v1"


class _RequestBodyTooLarge(Exception):
    pass


class UploadBodyLimitMiddleware:
    """Reject oversized upload bodies before multipart parsing fills temp disk."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not path.endswith("/documents")
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError:
                declared_length = self.max_bytes + 1
            if declared_length > self.max_bytes:
                await self._reject(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": f"The upload request exceeds the {self.max_bytes}-byte body limit."},
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )
        await response(scope, receive, send)


class CaseCreate(BaseModel):
    """The declared facts a packet must state (README section 3)."""

    model_config = {"extra": "forbid"}

    case_id: str = Field(pattern=SAFE_ID_PATTERN)
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
    model_config = {"extra": "forbid"}

    criterion_id: str
    action: ReviewerAction
    corrected_result: Optional[CriterionResult] = None
    corrected_evidence_ids: Optional[list[str]] = None
    note: Optional[str] = None


def require_auth_configured(settings: Settings) -> None:
    """Defense in depth for settings objects built outside normal validation.

    `Settings` normally rejects this state first. Keeping the application-level
    guard prevents a future alternate settings loader from weakening startup.
    """
    if settings.environment in {"staging", "production"} and not settings.auth_enabled:
        raise RuntimeError(
            f"Refusing to start in environment={settings.environment}: OIDC authentication "
            "must be enabled and configured."
        )


CaseId = Annotated[str, ApiPath(pattern=SAFE_ID_PATTERN)]


def _case_dir(settings: Settings, organization_id: str, case_id: str) -> Path:
    """Resolve a tenant-scoped case directory and enforce containment."""
    root = (Path(settings.artifacts_dir) / "cases" / organization_id).resolve()
    directory = (root / case_id).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Case path escapes its organization root.")
    return directory


def _calendar_years_from_now(years: int) -> datetime:
    now = datetime.now(timezone.utc)
    try:
        return now.replace(year=now.year + years)
    except ValueError:  # February 29 into a non-leap year.
        return now.replace(month=2, day=28, year=now.year + years)


def create_app(
    settings: Optional[Settings] = None,
    *,
    authenticator: Optional[Authenticator] = None,
) -> FastAPI:
    """Build the application around one settings object.

    Every dependency below resolves from `active` rather than from the
    process-wide `get_settings()`. An app built with explicit settings has to
    honour them everywhere, or `create_app(settings)` is a lie that only shows
    up as a 503 in the one handler that reached past it.
    """
    active = settings or get_settings()
    require_auth_configured(active)
    configure_logging(active)
    active_authenticator = authenticator or build_authenticator(active)

    engine = engine_for(active) if active.database_url else None
    queue = (
        JobQueue(
            engine,
            retry_policy=RetryPolicy(
                max_attempts=active.job_max_attempts,
                initial_delay_seconds=active.effective_job_retry_initial_seconds,
                maximum_delay_seconds=active.effective_job_retry_max_seconds,
            ),
            retention_policy=RetentionPolicy(
                completed_years=active.completed_job_retention_years,
                failed_days=active.failed_job_retention_days,
            ),
        )
        if engine is not None
        else None
    )
    bearer = HTTPBearer(auto_error=False)

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

    def app_queue() -> JobQueue:
        if queue is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The durable job queue requires RXAUTH_DATABASE_URL.",
            )
        return queue

    def authorize(*required_roles: str):
        accepted = frozenset(required_roles)

        def dependency(
            credentials: HTTPAuthorizationCredentials | None = Security(bearer),
        ) -> Principal:
            token = credentials.credentials if credentials is not None else None
            try:
                principal = active_authenticator.authenticate(token)
            except AuthenticationError as exc:
                log_event("auth.rejected", error_type=type(exc).__name__)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=str(exc),
                    headers={"WWW-Authenticate": "Bearer"},
                ) from exc
            if not principal.permits_any(accepted):
                log_event(
                    "authorization.rejected",
                    organization_id=principal.organization_id,
                    actor_id=principal.subject,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="The authenticated identity does not have the required role.",
                )
            return principal

        return dependency

    can_write_cases = authorize(ROLE_CASE_WRITE)
    can_read_cases = authorize(ROLE_CASE_READ, ROLE_CASE_WRITE, ROLE_REVIEW)
    can_review_cases = authorize(ROLE_REVIEW)

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
    app.add_middleware(
        UploadBodyLimitMiddleware,
        max_bytes=active.upload_max_file_bytes + active.upload_multipart_overhead_bytes,
    )
    app.state.settings = active
    app.state.job_queue = queue
    app.state.job_runner = queue  # Compatibility name for callers migrating from Stage 2.
    app.state.engine = engine
    app.state.job_worker = (
        JobWorker(
            queue,
            {"case_run": build_case_job_handler(active, engine)},
            lease_seconds=active.job_lease_seconds,
            heartbeat_seconds=active.job_heartbeat_seconds,
            poll_seconds=active.job_poll_seconds,
        )
        if queue is not None and engine is not None
        else None
    )

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
    def create_case(
        payload: CaseCreate,
        settings: Settings = Depends(require_database),
        principal: Principal = Depends(can_write_cases),
    ) -> dict[str, Any]:
        """Write the case manifest. Documents are uploaded separately."""
        manifest = CaseManifest.model_validate(payload.model_dump())
        with transaction() as session:
            existing = load_case_record(
                session,
                organization_id=principal.organization_id,
                case_id=manifest.case_id,
            )
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Case {manifest.case_id!r} already exists.",
                )
            create_case_record(
                session,
                organization_id=principal.organization_id,
                case_id=manifest.case_id,
                manifest=manifest.model_dump(mode="json"),
            )

        directory = _case_dir(settings, principal.organization_id, manifest.case_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "case.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8", newline="\n"
        )
        log_event(
            "case.created",
            case_id=manifest.case_id,
            organization_id=principal.organization_id,
            actor_id=principal.subject,
        )
        return {"case_id": manifest.case_id, "documents": 0}

    @app.post("/cases/{case_id}/documents", status_code=status.HTTP_201_CREATED)
    def upload_document(
        case_id: CaseId,
        file: UploadFile = File(...),
        settings: Settings = Depends(require_database),
        principal: Principal = Depends(can_write_cases),
    ) -> dict[str, Any]:
        """Store one document, in object storage and on the working directory.

        Both, on purpose: object storage is the record of what was submitted,
        and the pipeline reads files from a directory. The stored copy is the
        one that outlives the container.
        """
        with transaction() as session:
            case_record = load_case_record(
                session, organization_id=principal.organization_id, case_id=case_id
            )
            if case_record is None:
                raise HTTPException(
                    status_code=404, detail=f"No case {case_id!r}. Create it first."
                )
            document_count, case_size = case_upload_usage(
                session, organization_id=principal.organization_id, case_id=case_id
            )
            filenames = {
                document.filename
                for document in list_uploaded_documents(
                    session, organization_id=principal.organization_id, case_id=case_id
                )
            }
        if document_count >= settings.upload_max_documents_per_case:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"The case already has the maximum of "
                    f"{settings.upload_max_documents_per_case} documents."
                ),
            )

        directory = _case_dir(settings, principal.organization_id, case_id)
        try:
            staged = stage_upload(file.file, file.filename, directory, settings)
        except UploadTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
            ) from exc
        except UploadConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except UploadValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

        if staged.filename in filenames:
            staged.discard()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A document named {staged.filename!r} already exists in this case.",
            )
        if case_size + staged.size_bytes > settings.upload_max_case_bytes:
            staged.discard()
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"The upload would exceed the {settings.upload_max_case_bytes}-byte case limit.",
            )

        document_id = uuid.uuid4().hex
        store = build_object_store(settings)
        key = document_key(
            case_id,
            document_id,
            staged.filename,
            organization_id=principal.organization_id,
            prefix=settings.s3_prefix,
        )
        retain_until = _calendar_years_from_now(settings.original_document_retention_years)
        try:
            with staged.temporary_path.open("rb") as handle:
                stored = store.put(key, handle, retain_until=retain_until)
            with transaction() as session:
                case_record = load_case_record(
                    session,
                    organization_id=principal.organization_id,
                    case_id=case_id,
                    for_update=True,
                )
                if case_record is None:
                    raise HTTPException(status_code=404, detail=f"No case {case_id!r}.")
                current_count, current_size = case_upload_usage(
                    session, organization_id=principal.organization_id, case_id=case_id
                )
                if current_count >= settings.upload_max_documents_per_case:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="The case document limit was reached by another upload.",
                    )
                if current_size + staged.size_bytes > settings.upload_max_case_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="The case byte limit was reached by another upload.",
                    )
                save_uploaded_document(
                    session,
                    document_id=document_id,
                    case_record_id=case_record.id,
                    organization_id=principal.organization_id,
                    case_id=case_id,
                    filename=staged.filename,
                    media_type=staged.media_type,
                    size_bytes=staged.size_bytes,
                    sha256=staged.sha256,
                    storage_key=stored.key,
                    retain_until=retain_until,
                )
        except Exception:
            store.delete(key)
            staged.discard()
            raise

        staged.commit()

        log_event(
            "document.stored",
            case_id=case_id,
            organization_id=principal.organization_id,
            actor_id=principal.subject,
        )
        return {
            "case_id": case_id,
            "document_id": document_id,
            "filename": staged.filename,
            "media_type": staged.media_type,
            "storage_key": stored.key,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }

    @app.post("/cases/{case_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    def start_run(
        case_id: CaseId,
        settings: Settings = Depends(require_database),
        queue: JobQueue = Depends(app_queue),
        principal: Principal = Depends(can_write_cases),
    ) -> dict[str, Any]:
        """Accept a run and return a job to poll.

        202, not 200: the work has been accepted, not done. A case run is
        seconds at best and minutes on a scanned packet.
        """
        with transaction() as session:
            case_record = load_case_record(
                session, organization_id=principal.organization_id, case_id=case_id
            )
            if case_record is None:
                raise HTTPException(status_code=404, detail=f"No case {case_id!r}.")
            document_count, _ = case_upload_usage(
                session, organization_id=principal.organization_id, case_id=case_id
            )
        if document_count == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Case {case_id!r} has no uploaded documents.",
            )

        context = RunContext(case_id=case_id)
        job = queue.submit(
            "case_run",
            {"case_id": case_id},
            case_id=case_id,
            request_id=context.request_id,
            organization_id=principal.organization_id,
        )
        return job.as_dict()

    @app.get("/jobs/{job_id}")
    def get_job(
        job_id: str,
        queue: JobQueue = Depends(app_queue),
        principal: Principal = Depends(can_read_cases),
    ) -> dict[str, Any]:
        job = queue.get(job_id, organization_id=principal.organization_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No job {job_id!r} in this organization. Durable jobs remain queryable "
                    "until their configured retention period expires."
                ),
            )
        return job.as_dict()

    @app.get("/runs/{run_id}")
    def get_run(
        run_id: str,
        settings: Settings = Depends(require_database),
        principal: Principal = Depends(can_read_cases),
    ) -> dict[str, Any]:
        """The full run document — the same bytes written to reports/."""
        with transaction() as session:
            record = load_case_run(
                session, run_id=run_id, organization_id=principal.organization_id
            )
        if record is None:
            raise HTTPException(status_code=404, detail=f"No run {run_id!r}.")
        return record.payload

    @app.get("/cases/{case_id}/runs")
    def list_runs(
        case_id: CaseId,
        limit: int = 20,
        settings: Settings = Depends(require_database),
        principal: Principal = Depends(can_read_cases),
    ) -> dict[str, Any]:
        with transaction() as session:
            records = recent_case_runs(
                session,
                organization_id=principal.organization_id,
                case_id=case_id,
                limit=limit,
            )
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
        principal: Principal = Depends(can_review_cases),
    ) -> dict[str, Any]:
        """Record what a reviewer decided about one criterion (README section 16)."""
        with transaction() as session:
            record = load_case_run(
                session, run_id=run_id, organization_id=principal.organization_id
            )
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
                    reviewer_id=principal.subject,
                    action=payload.action,
                    corrected_result=payload.corrected_result,
                    corrected_evidence_ids=payload.corrected_evidence_ids,
                    note=payload.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            decision_id = save_reviewer_decision(
                session,
                decision,
                run_id=run_id,
                organization_id=principal.organization_id,
            )

        log_event(
            "reviewer.decision",
            case_id=record.case_id,
            criterion_id=payload.criterion_id,
            organization_id=principal.organization_id,
            actor_id=principal.subject,
        )
        return {"decision_id": decision_id, "run_id": run_id, "action": decision.action.value}

    @app.get("/cases/{case_id}/decisions")
    def list_decisions(
        case_id: CaseId,
        settings: Settings = Depends(require_database),
        principal: Principal = Depends(can_read_cases),
    ) -> dict[str, Any]:
        with transaction() as session:
            decisions = load_reviewer_decisions(
                session, organization_id=principal.organization_id, case_id=case_id
            )
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
