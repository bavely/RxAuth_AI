"""Relational persistence for case runs (roadmap Stage 2).

The domain models in `models.py` are already the schema — typed, validated, and
carrying their own provenance. These tables map them to Postgres without
redefining them: each row keeps the identifying columns a query needs
(case, document, criterion, result) and stores the full validated object as
JSON alongside, so a read round-trips to the exact Pydantic model the pipeline
produced rather than to a lossy reconstruction of it.

That is a deliberate trade. Fully normalising `Evidence` would give richer SQL
and a second definition of every shape to keep in sync with `models.py`; the
project's whole discipline is that there is one definition of a shape. Columns
exist where something is filtered or joined on, and the JSON is the truth.

**No pgvector yet.** The roadmap names it, and retrieval is still TF-IDF
computed in memory — nothing reads embeddings from a store. Persisting vectors
that no code queries would be unmeasured infrastructure, and keeping the schema
free of dialect-specific types is what lets this suite run on SQLite locally and
Postgres in CI. `policy_retrieval.EmbeddingBackend` remains the seam.
"""

from .repository import (
    CaseRecord,
    CaseRunRecord,
    UploadedDocumentRecord,
    case_upload_usage,
    create_case_record,
    list_uploaded_documents,
    load_case_record,
    load_case_run,
    load_reviewer_decisions,
    recent_case_runs,
    save_case_run,
    save_reviewer_decision,
    save_uploaded_document,
)
from .session import (
    DatabaseNotConfiguredError,
    create_all,
    engine_for,
    session_scope,
    sessionmaker_for,
)
from .tables import (
    Base,
    CaseRow,
    CaseRunRow,
    CriterionEvaluationRow,
    DocumentRow,
    JobRow,
    ReviewerDecisionRow,
    UploadedDocumentRow,
)

__all__ = [
    "Base",
    "CaseRecord",
    "CaseRow",
    "CaseRunRecord",
    "CaseRunRow",
    "CriterionEvaluationRow",
    "DatabaseNotConfiguredError",
    "DocumentRow",
    "JobRow",
    "ReviewerDecisionRow",
    "UploadedDocumentRecord",
    "UploadedDocumentRow",
    "case_upload_usage",
    "create_case_record",
    "create_all",
    "engine_for",
    "load_case_run",
    "load_case_record",
    "list_uploaded_documents",
    "load_reviewer_decisions",
    "recent_case_runs",
    "save_case_run",
    "save_reviewer_decision",
    "save_uploaded_document",
    "session_scope",
    "sessionmaker_for",
]
