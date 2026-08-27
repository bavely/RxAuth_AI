"""Tests for policy criteria extraction (README section 11).

README section 6's hybrid principle is what these hold: prose becomes a
structured rule here, and the comparison happens in plain Python afterwards.
The tests that matter most are about what the extractor refuses to do — drop a
requirement it cannot parse, read an exclusion as a criterion, or treat an ANY
list as an ALL list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rxauth_ai.criteria_extraction import (
    CRITERIA_EXTRACTOR_VERSION,
    CriteriaIssueKind,
    build_policy,
    extract_criteria,
    load_policy_documents,
)
from rxauth_ai.matching import evaluate_criterion
from rxauth_ai.models import Case, CriterionResult
from rxauth_ai.pipeline import run_pipeline

_POLICY_DIR = Path(__file__).resolve().parents[1] / "data" / "policies"


@pytest.fixture(scope="module")
def documents():
    return load_policy_documents(_POLICY_DIR)


def _structure(criterion):
    return (
        criterion.criterion_type,
        criterion.medication,
        criterion.operator,
        criterion.expected_value,
        criterion.unit,
        criterion.required_outcome,
    )


def test_prose_becomes_the_structured_rule_the_matcher_evaluates(documents):
    """README section 6's worked example: "at least 12 weeks of Drug A" has to
    arrive at the matcher as operator, value, and unit — never as text."""
    result = extract_criteria(documents["PA-104:2026-01"])

    assert [_structure(c) for c in result.criteria] == [
        ("diagnosis", None, "exists", None, None, None),
        ("previous_therapy", "Drug A", ">=", 12.0, "weeks", None),
        ("previous_therapy", "Drug A", ">=", 12.0, "weeks", "inadequate_response"),
        ("lab_a1c", None, "<", 8.0, "percent", None),
        ("screening_doc", None, "exists", None, None, None),
        ("therapy_duration", None, ">=", 8.0, "weeks", None),
    ]


def test_comparator_words_map_to_operators(documents):
    """Payers write comparisons in words. Every mapping is one auditable table,
    and reading "no greater than" as "greater than" would invert a threshold."""
    operators = {
        "PA-118:2025-03": ("lab_alt", "<=", 60.0),
        "PA-233:2025-07": ("lab_crp", ">", 5.0),
        "PA-207:2025-09": ("lab_egfr", ">=", 45.0),
        "PA-402:2025-11": ("lab_ldl_cholesterol", "<", 100.0),
    }
    for key, (criterion_type, operator, value) in operators.items():
        criterion = next(
            c
            for c in extract_criteria(documents[key]).criteria
            if c.criterion_type == criterion_type
        )
        assert (criterion.operator, criterion.expected_value) == (operator, value)


def test_every_criterion_cites_the_policy_span_it_was_read_from(documents):
    """README section 11: original text, payer, version, effective date, page,
    and the extraction version travel with the requirement."""
    policy, _ = build_policy(documents["PA-104:2026-01"])

    for criterion in policy.criteria:
        assert criterion.provenance.document_id == "PA-104"
        assert criterion.provenance.filename == "PA-104_2026-01.txt"
        assert criterion.provenance.page == 3
        assert criterion.provenance.start_char is not None
        assert criterion.description in (criterion.provenance.source_text or "")
        assert criterion.extractor_version == CRITERIA_EXTRACTOR_VERSION
    assert policy.version == "2026-01"
    assert policy.effective_date == "2026-01-01"


def test_a_requirement_no_rule_can_structure_is_kept_and_flagged(documents):
    """The dangerous failure is the quiet one: a policy evaluated against fewer
    criteria than it states reads as readier than it is."""
    result = extract_criteria(documents["PA-402:2025-11"])
    unstructured = [c for c in result.criteria if c.criterion_type == "unstructured"]

    assert len(unstructured) == 1
    assert "formulary alternative" in unstructured[0].description
    assert unstructured[0].provenance.source_text
    assert [issue.kind for issue in result.issues] == [CriteriaIssueKind.UNSTRUCTURED_REQUIREMENT]
    # All four requirements survive; one of them simply cannot be checked.
    assert len(result.criteria) == 4


def test_an_unstructured_criterion_routes_to_a_human_not_to_missing(documents):
    """ "No evidence found" would be a false statement about the case, when the
    truth is that the rule set could not express the requirement."""
    result = extract_criteria(documents["PA-402:2025-11"])
    criterion = next(c for c in result.criteria if c.criterion_type == "unstructured")
    empty_case = Case(
        id="T",
        patient_synthetic_id="S",
        payer="Cascade Benefit Plan",
        medication="Drug C",
        indication="Example Condition B",
        pa_required=True,
    )

    evaluation = evaluate_criterion(criterion, empty_case)

    assert evaluation.result is CriterionResult.HUMAN_REVIEW_REQUIRED
    assert "could not be converted" in evaluation.explanation


def test_exclusions_are_parsed_separately_and_never_join_the_criteria(documents):
    """ "Not covered when ANY of the following applies" reads almost exactly
    like a coverage list and means the opposite."""
    policy, result = build_policy(documents["PA-104:2026-01"])

    assert len(policy.exclusions) == 2
    assert all(exclusion.polarity == "exclusion" for exclusion in policy.exclusions)
    assert all(criterion.polarity == "inclusion" for criterion in policy.criteria)
    assert {e.id for e in policy.exclusions}.isdisjoint({c.id for c in policy.criteria})
    assert result.connective == "all"


def test_a_disjunctive_policy_is_refused_rather_than_scored_as_a_conjunction(documents):
    """Evaluating an ANY list as an AND fails a case the policy actually covers."""
    policy, result = build_policy(documents["PA-341:2026-02"])
    case = Case(
        id="T",
        patient_synthetic_id="S",
        payer="Northwind Health",
        medication="dupilumab",
        indication="Example Atopic Condition",
        pa_required=True,
    )

    assert result.connective == "any"
    assert CriteriaIssueKind.DISJUNCTIVE_SECTION in {issue.kind for issue in result.issues}
    with pytest.raises(ValueError, match="joins its coverage criteria with 'any'"):
        run_pipeline(case, policy)


def test_the_criteria_of_every_corpus_policy_are_structured_or_flagged(documents):
    """Nothing falls through the gap between "parsed" and "reported"."""
    for key, document in documents.items():
        result = extract_criteria(document)
        unstructured = {c.id for c in result.criteria if c.criterion_type == "unstructured"}
        flagged = {
            issue.criterion_id
            for issue in result.issues
            if issue.kind is CriteriaIssueKind.UNSTRUCTURED_REQUIREMENT
        }
        assert unstructured == flagged, f"{key} has an unreported unstructured criterion"
