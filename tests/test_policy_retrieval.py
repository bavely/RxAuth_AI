"""Tests for payer-policy retrieval (README section 10).

The design claim under test is that metadata filtering comes *first* and that
retrieval would rather return nothing than return the closest available policy.
The corpus is built to punish the alternative: `PA-207` covers the same drug
and indication as `PA-104` for a different payer, in near-identical wording.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rxauth_ai.policy_retrieval import (
    AmbiguousPolicyError,
    LsaEmbedding,
    PolicyIndex,
    PolicyNotFoundError,
    PolicyQuery,
    TfidfEmbedding,
    build_index,
    resolve_policy_document,
)

_POLICY_DIR = Path(__file__).resolve().parents[1] / "data" / "policies"


@pytest.fixture(scope="module")
def index():
    return build_index(_POLICY_DIR)


def _resolve(index, **overrides):
    kwargs = {
        "payer": "Example Health Plan",
        "medication": "Drug A",
        "indication": "Example Condition",
        "as_of_date": "2026-01-14",
    }
    kwargs.update(overrides)
    return resolve_policy_document(index, **kwargs)


def test_the_metadata_filter_selects_the_policy(index):
    document, result = _resolve(index)

    assert document.key == "PA-104:2026-01"
    assert result.candidate_policies == ["PA-104:2026-01"]


def test_another_payers_near_identical_policy_is_never_returned(index):
    """PA-207 is the same drug and the same indication in almost the same
    words. Text similarity alone cannot tell them apart; the payer can."""
    ours, _ = _resolve(index)
    theirs, _ = _resolve(index, payer="Northwind Health")

    assert ours.policy_id == "PA-104"
    assert theirs.policy_id == "PA-207"


def test_the_request_date_chooses_between_two_versions_of_one_policy(index):
    current, _ = _resolve(index, as_of_date="2026-01-14")
    superseded, _ = _resolve(index, as_of_date="2025-06-01")

    assert current.version == "2026-01"
    assert superseded.version == "2024-06"


def test_a_drug_the_corpus_does_not_cover_returns_nothing(index):
    """ "Some policy" is not a safe answer to "which policy applies"."""
    with pytest.raises(PolicyNotFoundError, match="No payer policy found"):
        _resolve(index, medication="Drug Z")


def test_a_date_before_every_version_returns_nothing(index):
    with pytest.raises(PolicyNotFoundError):
        _resolve(index, as_of_date="2024-01-01")


def test_a_policy_that_is_not_yet_effective_is_not_returned(index):
    with pytest.raises(PolicyNotFoundError):
        _resolve(
            index,
            payer="Northwind Health",
            medication="Dupixent",
            indication="Example Atopic Condition",
            as_of_date="2026-01-14",
        )

    document, _ = _resolve(
        index,
        payer="Northwind Health",
        medication="Dupixent",
        indication="Example Atopic Condition",
        as_of_date="2026-03-01",
    )
    assert document.key == "PA-341:2026-02"


def test_a_brand_name_in_the_case_finds_the_generic_in_the_policy(index):
    document, _ = _resolve(
        index,
        payer="Example Health Plan",
        medication="Humira",
        indication="Example Inflammatory Condition",
    )

    assert document.key == "PA-233:2025-07"


def test_overlapping_version_windows_are_refused_rather_than_ranked(index):
    """Undated, both versions of PA-104 are in force. Picking the newest would
    be a guess about which requirements the payer would apply."""
    with pytest.raises(AmbiguousPolicyError, match="will not choose between"):
        _resolve(index, as_of_date=None)


def test_an_empty_filter_result_does_not_fall_back_to_unfiltered_search(index):
    result = index.search(
        PolicyQuery(text="coverage criteria", payer="Nonexistent Plan", medication="Drug A")
    )

    assert result.chunks == []
    assert result.selected_policy_key is None
    assert "falling back" in (result.rejection_reason or "")


def test_similarity_ranks_the_passages_inside_the_selected_policy(index):
    result = index.search(
        PolicyQuery(
            text="What A1c threshold applies?",
            payer="Example Health Plan",
            medication="Drug A",
            indication="Example Condition",
            as_of_date="2026-01-14",
            top_k=3,
        )
    )

    assert result.chunks
    assert {chunk.chunk.policy_id for chunk in result.chunks} == {"PA-104"}
    assert any("A1c" in chunk.chunk.text for chunk in result.chunks)
    assert [chunk.rank for chunk in result.chunks] == [1, 2, 3]


def test_every_retrieved_chunk_carries_a_usable_citation(index):
    _, result = _resolve(index)

    for retrieved in result.chunks:
        chunk = retrieved.chunk
        assert chunk.policy_id in chunk.citation
        assert chunk.policy_version in chunk.citation
        assert str(chunk.page) in chunk.citation
        assert chunk.end_char > chunk.start_char


def test_the_result_records_which_embedding_produced_it(index):
    _, result = _resolve(index)

    assert result.embedding_model == "tfidf-v1"
    assert result.chunk_strategy == "section+enumerated-item"


def test_an_alternative_embedding_backend_drops_in_unchanged():
    """README section 10's pgvector deployment swaps this one method surface;
    filtering, ranking, and citation must not care which backend is in use."""
    lsa = build_index(_POLICY_DIR, embedding=LsaEmbedding(n_components=16))
    document, result = _resolve(lsa)

    assert document.key == "PA-104:2026-01"
    assert result.embedding_model.startswith("tfidf-lsa-v1")
    assert result.chunks


def test_similarity_only_mode_considers_every_policy(index):
    """The ablation arm the retrieval benchmark measures. It exists to be
    compared against, not to be used for a case."""
    result = index.search(
        PolicyQuery(text="Drug A Example Condition coverage criteria", mode="similarity_only")
    )

    assert len(result.candidate_policies) == len(index.documents)
    assert result.chunks


def test_an_index_cannot_be_built_from_an_empty_corpus():
    with pytest.raises(ValueError, match="empty corpus"):
        PolicyIndex([], embedding=TfidfEmbedding())
