"""Tests for the hardened Phase 1.5 classification benchmark."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from rxauth_ai.build_dataset import build_dataset
from rxauth_ai.classifier import DocumentClassifier, load_manifest, train_and_evaluate
from rxauth_ai.models import DocumentType


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_dataset_covers_taxonomy_grouped_splits_and_contract():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        rows = _rows(build_dataset(out_dir, per_class=20, seed=1))

        assert len(rows) == 20 * len(DocumentType)
        assert {row["label"] for row in rows} == {
            document_type.value for document_type in DocumentType
        }
        assert {row["split"] for row in rows} == {"train", "val", "test", "challenge"}
        assert all(row["case_id"] and row["template_family_id"] for row in rows)
        for row in rows:
            assert (out_dir / row["relative_path"]).exists()

        for left in ("train", "val", "test", "challenge"):
            left_rows = [row for row in rows if row["split"] == left]
            for right in ("train", "val", "test", "challenge"):
                if left >= right:
                    continue
                right_rows = [row for row in rows if row["split"] == right]
                assert {row["case_id"] for row in left_rows}.isdisjoint(
                    {row["case_id"] for row in right_rows}
                )
                assert {row["template_family_id"] for row in left_rows}.isdisjoint(
                    {row["template_family_id"] for row in right_rows}
                )


def test_dataset_is_reproducible_given_same_seed():
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        path_a = build_dataset(Path(tmp_a), per_class=10, seed=7)
        path_b = build_dataset(Path(tmp_b), per_class=10, seed=7)

        assert path_a.read_bytes() == path_b.read_bytes()
        assert (Path(tmp_a) / "documents" / "clinical_note" / "doc_0009.txt").read_bytes() == (
            Path(tmp_b) / "documents" / "clinical_note" / "doc_0009.txt"
        ).read_bytes()


def test_dataset_rebuild_removes_stale_generated_documents():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        build_dataset(out_dir, per_class=20, seed=7)
        build_dataset(out_dir, per_class=10, seed=7)

        generated = list((out_dir / "documents").glob("*/doc_*.txt"))
        assert len(generated) == 10 * len(DocumentType)


def test_classifier_evaluates_challenge_and_persists_bundle():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        build_dataset(out_dir, per_class=30, seed=42)
        splits = load_manifest(out_dir)
        results = train_and_evaluate(splits)

        n_classes = len(DocumentType)
        assert results["test_accuracy"] > (1.0 / n_classes) * 2
        assert results["n_challenge"] > 0
        assert set(results["labels"]) == {document_type.value for document_type in DocumentType}
        assert results["confusion_matrix"].shape == (n_classes, n_classes)
        assert 0 <= results["evaluations"]["challenge"]["macro_f1"] <= 1

        # The artifact is a self-describing directory, not a pickle: see registry.py.
        artifact = out_dir / "classifier_artifact"
        results["classifier"].save(artifact)
        assert sorted(item.name for item in artifact.iterdir()) == [
            "manifest.json",
            "model.json",
            "weights.npz",
        ]
        restored = DocumentClassifier.load(artifact)
        before = results["classifier"].predict_text(splits["test"].texts[0])
        after = restored.predict_text(splits["test"].texts[0])
        assert after == before


def test_high_confidence_threshold_routes_prediction_to_review():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        build_dataset(out_dir, per_class=20, seed=42)
        splits = load_manifest(out_dir)
        classifier = train_and_evaluate(splits, confidence_threshold=1.0)["classifier"]

        prediction = classifier.predict_text("unfamiliar short document")
        assert prediction.requires_human_review
