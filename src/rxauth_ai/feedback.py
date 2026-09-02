"""Reviewer corrections as engineering data (README section 16).

A reviewer who changes an answer knows something the system did not. Today
that knowledge exists for as long as the browser tab is open. This module is
the schema and the store that keep it: what the system said, what the reviewer
said instead, which evidence they were looking at, and which versions of which
components produced the original — because "did the new matcher fix the thing
reviewers kept correcting?" is unanswerable without the last part.

There is no UI yet. The schema comes first on purpose: a correction captured
without its model version, or without the evidence the reviewer saw, cannot be
replayed later and is not worth storing. Designing that after the interface
exists means discovering the gap when the first month of corrections turns out
to be unusable.

The payoff is `to_matching_gold_record`. A correction is exactly the two
fields the matching gold set needs — the result that should have been returned
and the evidence that should have been cited — so a reviewer disagreeing with
the matcher can become a regression case rather than an anecdote.

**On PHI.** A corrected value is a clinical fact, and in a real deployment this
store holds PHI. It is a file of synthetic corrections here. README section 19
governs what changes before it holds anything else: encryption, access
control, retention, and a BAA covering wherever it lives.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from .config import get_settings
from .models import Case, Criterion, CriterionEvaluation, CriterionResult

FEEDBACK_SCHEMA_VERSION = "reviewer-feedback-v1"
DEFAULT_FEEDBACK_PATH = get_settings().feedback_path


class ReviewerAction(str, Enum):
    """What the reviewer did with one criterion result.

    `ACCEPTED` is stored, not discarded. Agreement is evidence too: a component
    that is never corrected and a component nobody ever looked at are
    indistinguishable unless the confirmations are recorded.
    """

    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class ReviewerDecision(BaseModel):
    """One reviewer's verdict on one criterion evaluation."""

    schema_version: str = FEEDBACK_SCHEMA_VERSION
    case_id: str
    criterion_id: str
    reviewer_id: str = Field(description="Pseudonymous reviewer identifier, never a name.")
    action: ReviewerAction
    recorded_at: str = Field(description="ISO-8601 UTC timestamp.")

    original_result: CriterionResult
    original_evidence_ids: list[str] = Field(default_factory=list)
    original_confidence: float = Field(ge=0.0, le=1.0)

    corrected_result: Optional[CriterionResult] = None
    corrected_evidence_ids: Optional[list[str]] = None
    note: Optional[str] = Field(
        default=None, description="Why the reviewer disagreed, in their own words."
    )

    # Which components produced the original answer. Without these a correction
    # cannot be attributed, and "has this improved?" stays unanswerable.
    matcher_version: str
    normalization_version: str
    extractor_version: Optional[str] = None
    criteria_extractor_version: Optional[str] = None
    generator_version: Optional[str] = None
    prompt_version: Optional[str] = None

    @model_validator(mode="after")
    def validate_correction_is_actually_a_correction(self) -> ReviewerDecision:
        if self.action is ReviewerAction.CORRECTED:
            if self.corrected_result is None and self.corrected_evidence_ids is None:
                raise ValueError(
                    "A CORRECTED decision must state what the answer should have been — "
                    "either corrected_result or corrected_evidence_ids."
                )
            unchanged = (
                self.corrected_result in (None, self.original_result)
                and self.corrected_evidence_ids is not None
                and set(self.corrected_evidence_ids) == set(self.original_evidence_ids)
            )
            if unchanged:
                raise ValueError(
                    "A CORRECTED decision that changes nothing should be recorded as ACCEPTED."
                )
        return self

    @property
    def final_result(self) -> CriterionResult:
        """What the reviewer left standing."""
        return self.corrected_result or self.original_result

    @property
    def final_evidence_ids(self) -> list[str]:
        if self.corrected_evidence_ids is None:
            return list(self.original_evidence_ids)
        return list(self.corrected_evidence_ids)


def decision_from_evaluation(
    evaluation: CriterionEvaluation,
    *,
    reviewer_id: str,
    action: ReviewerAction,
    corrected_result: Optional[CriterionResult] = None,
    corrected_evidence_ids: Optional[list[str]] = None,
    note: Optional[str] = None,
    extractor_version: Optional[str] = None,
    criteria_extractor_version: Optional[str] = None,
    generator_version: Optional[str] = None,
    prompt_version: Optional[str] = None,
    recorded_at: Optional[str] = None,
) -> ReviewerDecision:
    """Build a decision from the evaluation the reviewer was looking at.

    Taking the evaluation rather than loose fields means the versions and the
    original answer are copied from the record instead of retyped, so a
    correction cannot be filed against an answer that was never given.
    """
    return ReviewerDecision(
        case_id=evaluation.case_id,
        criterion_id=evaluation.criterion_id,
        reviewer_id=reviewer_id,
        action=action,
        recorded_at=recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        original_result=evaluation.result,
        original_evidence_ids=list(evaluation.supporting_evidence_ids),
        original_confidence=evaluation.confidence,
        corrected_result=corrected_result,
        corrected_evidence_ids=corrected_evidence_ids,
        note=note,
        matcher_version=evaluation.matcher_version,
        normalization_version=evaluation.normalization_version,
        extractor_version=extractor_version,
        criteria_extractor_version=criteria_extractor_version,
        generator_version=generator_version,
        prompt_version=prompt_version,
    )


class FeedbackStore:
    """Append-only JSONL store.

    Append-only because a correction that can be edited later is not a record
    of what a reviewer thought at the time. Superseding a decision means
    appending another one, which keeps the disagreement visible.
    """

    def __init__(self, path: Path = DEFAULT_FEEDBACK_PATH) -> None:
        self.path = Path(path)

    def append(self, decision: ReviewerDecision) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(decision.model_dump_json() + "\n")

    def load(self) -> list[ReviewerDecision]:
        if not self.path.is_file():
            return []
        decisions = []
        for number, raw in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                decisions.append(ReviewerDecision.model_validate_json(raw))
            except Exception as exc:
                raise ValueError(f"Invalid reviewer decision on line {number}: {exc}") from exc
        return decisions

    def for_case(self, case_id: str) -> list[ReviewerDecision]:
        return [decision for decision in self.load() if decision.case_id == case_id]

    def disagreements(self) -> list[ReviewerDecision]:
        """Only the decisions where the reviewer changed the answer."""
        return [
            decision
            for decision in self.load()
            if decision.action in (ReviewerAction.CORRECTED, ReviewerAction.REJECTED)
        ]


def correction_rate_by_version(decisions: list[ReviewerDecision]) -> dict[str, float]:
    """How often each matcher version was corrected.

    This is the question the version fields exist to answer, and the reason
    `ACCEPTED` decisions are stored: the denominator is every decision made
    against that version, not just the ones that went wrong.
    """
    totals: dict[str, int] = {}
    corrected: dict[str, int] = {}
    for decision in decisions:
        version = decision.matcher_version
        totals[version] = totals.get(version, 0) + 1
        if decision.action in (ReviewerAction.CORRECTED, ReviewerAction.REJECTED):
            corrected[version] = corrected.get(version, 0) + 1
    return {version: corrected.get(version, 0) / count for version, count in sorted(totals.items())}


def to_matching_gold_record(
    decision: ReviewerDecision,
    case: Case,
    criterion: Criterion,
    *,
    split: str = "validation",
) -> dict[str, Any]:
    """Turn one correction into a `data/matching_gold.jsonl` record.

    This is where a reviewer's disagreement stops being an anecdote. The
    resulting record replays the same criterion against the same evidence and
    asserts the answer the reviewer gave, so the next benchmark run either
    reproduces their judgement or reports that it does not.

    New records default to the **validation** split. A correction is
    development signal — somebody will change code because of it — and
    `docs/matching-gold.md` requires anything that drives implementation to
    live in validation so the test split stays frozen.
    """
    cited = set(decision.final_evidence_ids)
    return {
        "match_id": f"FEEDBACK-{decision.case_id}-{decision.criterion_id}",
        "split": split,
        "medication": case.medication,
        "indication": case.indication,
        "criterion": {
            "criterion_type": criterion.criterion_type,
            "medication": criterion.medication,
            "operator": criterion.operator,
            "expected_value": criterion.expected_value,
            "unit": criterion.unit,
            "required_outcome": criterion.required_outcome,
            "confidence": criterion.confidence,
            "source_text": criterion.provenance.source_text or criterion.description,
        },
        "evidence": [
            {
                "id": item.id,
                "evidence_type": item.evidence_type,
                "medication": item.medication,
                "text_value": item.text_value,
                "value": item.value,
                "unit": item.unit,
                "outcome": item.outcome,
                "confidence": item.confidence,
                "source_text": item.provenance.source_text or item.id,
            }
            for item in case.evidence
        ],
        "expected_result": decision.final_result.value,
        "expected_evidence_ids": [item.id for item in case.evidence if item.id in cited],
        "note": (
            f"Reviewer {decision.reviewer_id} recorded {decision.action.value} against "
            f"{decision.matcher_version} on {decision.recorded_at}."
            + (f" {decision.note}" if decision.note else "")
        ),
    }


def export_gold_records(path: Path, records: list[dict[str, Any]]) -> None:
    """Write candidate gold records for review before they join the real set."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
        newline="\n",
    )
