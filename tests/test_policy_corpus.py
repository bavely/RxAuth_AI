"""Tests for policy parsing, section detection, and chunking (README section 10).

The contract these hold is that a chunk stays *citable*: it knows its policy
version, its section, its page, and a character span that still indexes the
page it was cut from. A chunk that cannot be pointed at is not evidence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from rxauth_ai.policy_corpus import (
    MANIFEST_FILENAME,
    PolicyCorpusError,
    _clean,
    _split_pages,
    load_corpus,
    parse_policy,
    publish_corpus,
    read_manifest,
    read_published_source,
)

_POLICY_DIR = Path(__file__).resolve().parents[1] / "data" / "policies"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(_POLICY_DIR)


def test_every_policy_declares_the_metadata_retrieval_filters_on(corpus):
    for document in corpus:
        assert document.policy_id
        assert document.payer
        assert document.normalized_medication
        assert document.indication
        assert document.effective_date
        assert document.version


def test_every_chunk_span_still_indexes_the_page_it_was_cut_from(corpus):
    """The whole citation contract in one assertion: if an offset drifts, the
    quoted text and the document stop agreeing and nothing downstream notices."""
    for document in corpus:
        text = _clean((_POLICY_DIR / document.filename).read_text(encoding="utf-8"))
        pages = {page.number: page.text for page in _split_pages(text)}
        for chunk in document.chunks:
            assert pages[chunk.page][chunk.start_char : chunk.end_char] == chunk.text


def test_an_enumerated_requirement_becomes_its_own_chunk(corpus):
    """README section 24 question 5: a reviewer asking what the policy requires
    about A1c should get the A1c line, not the section that contained it."""
    policy = next(document for document in corpus if document.key == "PA-104:2026-01")
    items = [chunk for chunk in policy.chunks_of_kind("criteria") if chunk.item_number]

    assert [chunk.item_number for chunk in items] == [1, 2, 3, 4, 5, 6]
    assert items[3].text == "4. The most recent A1c is below 8.0 percent."


def test_coverage_criteria_and_exclusions_are_different_section_kinds(corpus):
    """They are worded almost identically and mean opposite things."""
    policy = next(document for document in corpus if document.key == "PA-104:2026-01")

    assert [chunk.item_number for chunk in policy.chunks_of_kind("criteria") if chunk.item_number]
    exclusions = [chunk for chunk in policy.chunks_of_kind("exclusions") if chunk.item_number]
    assert len(exclusions) == 2
    assert all("not covered" not in chunk.text for chunk in policy.chunks_of_kind("criteria"))


def test_the_section_connective_is_read_rather_than_assumed(corpus):
    conjunctive = next(document for document in corpus if document.key == "PA-104:2026-01")
    disjunctive = next(document for document in corpus if document.key == "PA-341:2026-02")

    assert {chunk.connective for chunk in conjunctive.chunks_of_kind("criteria")} == {"all"}
    assert {chunk.connective for chunk in disjunctive.chunks_of_kind("criteria")} == {"any"}


def test_a_version_window_answers_which_policy_was_in_force(corpus):
    superseded = next(document for document in corpus if document.key == "PA-104:2024-06")
    current = next(document for document in corpus if document.key == "PA-104:2026-01")

    assert superseded.in_effect_on("2025-06-01")
    assert not superseded.in_effect_on("2026-01-14")
    assert current.in_effect_on("2026-01-14")
    assert not current.in_effect_on("2025-12-31")


def test_an_undated_question_considers_every_version(corpus):
    """`None` means the caller declined to say. Resolving that to the newest
    file would be a guess presented as a lookup."""
    assert all(document.in_effect_on(None) for document in corpus)


def test_a_brand_name_and_its_generic_resolve_to_the_same_policy(corpus):
    policy = next(document for document in corpus if document.key == "PA-233:2025-07")

    assert policy.medication == "adalimumab"
    assert policy.normalized_medication == "adalimumab"


def test_a_policy_without_filterable_metadata_is_rejected(tmp_path):
    path = tmp_path / "broken.txt"
    path.write_text(
        "SOME PAYER\nPolicy ID: PA-999\n\nSECTION 1. PURPOSE\nText.\n", encoding="utf-8"
    )

    with pytest.raises(PolicyCorpusError, match="missing required policy metadata"):
        parse_policy(path)


def test_two_documents_claiming_one_policy_version_are_rejected(tmp_path):
    body = (_POLICY_DIR / "PA-104_2026-01.txt").read_text(encoding="utf-8")
    (tmp_path / "a.txt").write_text(body, encoding="utf-8")
    (tmp_path / "b.txt").write_text(body, encoding="utf-8")

    with pytest.raises(PolicyCorpusError, match="already declared by"):
        load_corpus(tmp_path)


def test_an_empty_policy_directory_says_where_the_corpus_lives(tmp_path):
    with pytest.raises(PolicyCorpusError, match="data/policies"):
        load_corpus(tmp_path)


# Publication and source provenance (backlog task 601). Each test works on a copy
# of the shipped corpus, so the real directory and its manifest are never touched.

_PA_207_PUBLISHED_SHA256 = "fad5ecc34c54cc465cf02c0cad6ce8edc9364b2252bd0f66a6bf7af5c856c377"


@pytest.fixture()
def published_copy(tmp_path):
    destination = tmp_path / "policies"
    shutil.copytree(_POLICY_DIR, destination)
    return destination


def test_every_document_records_the_digest_of_its_exact_source_bytes(corpus):
    published = read_manifest(_POLICY_DIR)
    for document in corpus:
        raw = (_POLICY_DIR / document.filename).read_bytes()
        assert document.source_sha256 == hashlib.sha256(raw).hexdigest()
        assert document.source_bytes == len(raw)
        entry = published[document.key]
        assert entry["sha256"] == document.source_sha256
        assert entry["effective_date"] == document.effective_date
        assert entry["superseded_date"] == document.superseded_date


def test_the_pa_207_retrieval_trap_is_published_unchanged(corpus):
    """PA-207 is a deliberate near-miss. A republished manifest would follow an
    edit to it, so its digest is pinned here as well."""
    trap = next(document for document in corpus if document.policy_id == "PA-207")
    assert trap.source_sha256 == _PA_207_PUBLISHED_SHA256


def test_a_published_policy_whose_bytes_change_is_refused(published_copy):
    path = published_copy / "PA-104_2026-01.txt"
    path.write_bytes(path.read_bytes().replace(b"below 8.0 percent", b"below 9.0 percent"))

    with pytest.raises(PolicyCorpusError, match="no longer matches its published bytes"):
        load_corpus(published_copy)


def test_a_published_policy_that_disappears_is_refused(published_copy):
    (published_copy / "PA-104_2024-06.txt").unlink()

    with pytest.raises(PolicyCorpusError, match="PA-104:2024-06 .* is missing"):
        load_corpus(published_copy)


def test_a_corpus_with_no_manifest_has_nothing_published(published_copy):
    (published_copy / MANIFEST_FILENAME).unlink()

    with pytest.raises(PolicyCorpusError, match="rxauth-publish-policies"):
        load_corpus(published_copy)


def test_an_unknown_manifest_format_is_refused_rather_than_guessed_at(published_copy):
    path = published_copy / MANIFEST_FILENAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**manifest, "format_version": 99}), encoding="utf-8")

    with pytest.raises(PolicyCorpusError, match="format_version 99"):
        load_corpus(published_copy)


def test_a_new_version_loads_only_once_published_and_earlier_entries_never_change(
    published_copy,
):
    body = (published_copy / "PA-104_2026-01.txt").read_text(encoding="utf-8")
    (published_copy / "PA-104_2027-01.txt").write_text(
        body.replace("Version: 2026-01", "Version: 2027-01"), encoding="utf-8"
    )
    before = read_manifest(published_copy)

    with pytest.raises(PolicyCorpusError, match=r"PA-104:2027-01\) was never published"):
        load_corpus(published_copy)

    assert publish_corpus(published_copy) == ["PA-104:2027-01"]
    after = read_manifest(published_copy)
    assert {key: after[key] for key in before} == before
    assert any(document.key == "PA-104:2027-01" for document in load_corpus(published_copy))


def test_publishing_refuses_to_rewrite_a_published_version(published_copy):
    manifest = (published_copy / MANIFEST_FILENAME).read_bytes()
    path = published_copy / "PA-207_2025-09.txt"
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(PolicyCorpusError, match="immutable"):
        publish_corpus(published_copy)
    assert (published_copy / MANIFEST_FILENAME).read_bytes() == manifest


def test_a_recorded_digest_resolves_to_the_original_source(corpus):
    document = next(document for document in corpus if document.key == "PA-104:2024-06")

    raw = read_published_source(document.source_sha256, _POLICY_DIR)

    assert raw == (_POLICY_DIR / document.filename).read_bytes()


def test_a_source_that_changed_after_publication_is_not_served_as_the_original(published_copy):
    path = published_copy / "PA-118_2025-03.txt"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(path.read_bytes() + b"amended\n")

    with pytest.raises(PolicyCorpusError, match="no longer matches the digest"):
        read_published_source(digest, published_copy)
    with pytest.raises(PolicyCorpusError, match="No published policy version"):
        read_published_source("0" * 64, published_copy)
