"""Structured logging for the workflow (README section 18).

Section 18 specifies one structured log line per request carrying
`request_id, case_id, workflow_node, model, version, prompt_version, token
counts, latency_ms, estimated_cost, retrieved_document_ids, evaluation_result,
error_type`. Until now the project had no `logging` call at all — 100+ `print`
statements across 17 modules — so none of it existed.

Two decisions shape this module.

**Fields are allow-listed, not deny-listed.** `log_event` accepts only keys in
`LOGGABLE_FIELDS`; anything else is dropped and the drop is itself recorded.
A deny-list of "PHI-ish" names fails the moment somebody invents a new field,
and the failure mode is patient text in a log aggregator with no retention
policy. Fail-closed is the only defensible direction here, and it costs one
line per new field.

**Latency lives here, not in the reports.** `reports/case_<id>.json` is
committed as evidence and gated by `rxauth-check-reports`, so it deliberately
carries no timings. Wall-clock belongs in logs, which nobody diffs.

**What this does not solve.** An allow-list governs structured fields; it
cannot stop someone interpolating a quoted span into a message string. That is
why `log_event` takes no free-form message and why
`tests/test_observability.py` runs a whole case and asserts no evidence text
reached any handler. The test is the guarantee; the allow-list is the guard
rail that keeps the test passing.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator, Optional

from .config import Settings, get_settings
from .models import Provenance

LOG_SCHEMA_VERSION = "log-v1"
LOGGER_NAME = "rxauth"

#: Every field a log line may carry. README section 18's schema, plus the
#: identifiers the workflow needs to correlate a run. Adding a field is a
#: deliberate, reviewable act — which is the point.
LOGGABLE_FIELDS: frozenset[str] = frozenset(
    {
        # Correlation
        "request_id",
        "case_id",
        "schema_version",
        "workflow_version",
        # Stage
        "workflow_node",
        "node_status",
        "attempts",
        "criterion_id",
        # Versions
        "model",
        "model_version",
        "prompt_version",
        "extractor_version",
        "criteria_extractor_version",
        "matcher_version",
        "embedding_model",
        "generator_version",
        # Cost and timing
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
        "latency_ms",
        # Outcome
        "evaluation_result",
        "groundedness_gate",
        "retrieved_document_ids",
        "evidence_ids",
        "document_ids",
        "counts",
        "error_type",
        "error",
    }
)

#: Fields whose values are patient text. Named so the drop is explicit in the
#: record rather than silent, and so a reader of a log knows something was held
#: back rather than never existed.
PHI_BEARING_FIELDS: frozenset[str] = frozenset({"source_text", "text", "quote", "page_text"})


@dataclass
class RunContext:
    """Correlates every line emitted by one run."""

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    case_id: Optional[str] = None

    def fields(self) -> dict[str, Any]:
        values: dict[str, Any] = {"request_id": self.request_id}
        if self.case_id is not None:
            values["case_id"] = self.case_id
        return values


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the structured fields at the top level."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "event": record.getMessage(),
            "schema_version": LOG_SCHEMA_VERSION,
        }
        payload.update(getattr(record, "rxauth_fields", {}))
        return json.dumps(payload, default=str, sort_keys=True)


class TextFormatter(logging.Formatter):
    """Human-readable form for a terminal. Same fields, less punctuation."""

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "rxauth_fields", {})
        rendered = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
        return f"{record.levelname:<7} {record.getMessage():<28} {rendered}".rstrip()


def configure_logging(settings: Optional[Settings] = None) -> logging.Logger:
    """Install the formatter the settings ask for and return the logger.

    Replaces its own handler rather than adding one, so calling this twice in a
    process does not double every line.
    """
    active = settings or get_settings()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(active.log_level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if active.log_format == "json" else TextFormatter())
    logger.addHandler(handler)
    return logger


def _partition(fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    allowed = {key: value for key, value in fields.items() if key in LOGGABLE_FIELDS}
    dropped = sorted(key for key in fields if key not in LOGGABLE_FIELDS)
    return allowed, dropped


def log_event(event: str, /, *, logger: Optional[logging.Logger] = None, **fields: Any) -> None:
    """Emit one structured line.

    Takes an event *name*, not a message, and structured fields — so there is
    no format string for a quoted span to be interpolated into. Fields outside
    `LOGGABLE_FIELDS` are dropped and their names recorded under
    `dropped_fields`, so a mistake is visible in the log rather than invisible
    in the aggregator.
    """
    active = logger or logging.getLogger(LOGGER_NAME)
    allowed, dropped = _partition(fields)
    if dropped:
        allowed["error"] = (
            f"dropped un-allow-listed field(s): {', '.join(dropped)}. Add them to "
            "observability.LOGGABLE_FIELDS if they are safe to log."
        )
    active.info(event, extra={"rxauth_fields": allowed})


def loggable_provenance(provenance: Provenance) -> dict[str, Any]:
    """Reduce a citation to the parts that are safe to log.

    Document, filename, page, and character offsets locate a span precisely
    enough for a reviewer to open it. `source_text` is the span itself — the
    patient's words — and never leaves the report.
    """
    return {
        "document_id": provenance.document_id,
        "filename": provenance.filename,
        "page": provenance.page,
        "start_char": provenance.start_char,
        "end_char": provenance.end_char,
    }


@contextmanager
def timed(
    event: str,
    /,
    *,
    logger: Optional[logging.Logger] = None,
    **fields: Any,
) -> Iterator[dict[str, Any]]:
    """Time a block and log it once, whether it succeeds or raises.

    Yields a dict the caller can add fields to while the block runs, so a node
    can report what it produced without knowing in advance whether it will.
    """
    extra: dict[str, Any] = {}
    started = perf_counter()
    try:
        yield extra
    except Exception as exc:
        log_event(
            event,
            logger=logger,
            latency_ms=round((perf_counter() - started) * 1000, 3),
            node_status="failed",
            error_type=type(exc).__name__,
            error=str(exc),
            **fields,
            **extra,
        )
        raise
    log_event(
        event,
        logger=logger,
        latency_ms=round((perf_counter() - started) * 1000, 3),
        **fields,
        **extra,
    )
