"""Tests for the document classification baseline (main README §8, Phase 1).

Generates a small synthetic dataset into a temp directory (independent of the
checked-in data/documents/ corpus) so the test is fast and self-contained, then
verifies the dataset builder's contract and the classifier's train/eval path.

Run: python -m pytest -q   (or: python test_classifier.py)
"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from files.classifier import load_manifest, train_and_evaluate
from files.models import DocumentType

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "build_dataset", Path(__file__).parent / "data" / "build_dataset.py"
)
build_dataset_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_dataset_module)


def test_dataset_covers_full_taxonomy_and_all_splits():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        manifest_path = build_dataset_module.build_dataset(out_dir, per_class=20, seed=1)

        assert manifest_path.exists()
        with manifest_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 20 * len(DocumentType)
        assert {r["label"] for r in rows} == {d.value for d in DocumentType}
        assert {r["split"] for r in rows} == {"train", "val", "test"}
        for row in rows:
            assert (out_dir / row["relative_path"]).exists()


def test_dataset_is_reproducible_given_same_seed():
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        build_dataset_module.build_dataset(Path(tmp_a), per_class=10, seed=7)
        build_dataset_module.build_dataset(Path(tmp_b), per_class=10, seed=7)

        text_a = (Path(tmp_a) / "documents" / "clinical_note" / "doc_0000.txt").read_text()
        text_b = (Path(tmp_b) / "documents" / "clinical_note" / "doc_0000.txt").read_text()
        assert text_a == text_b


def test_classifier_trains_and_beats_random_baseline():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        build_dataset_module.build_dataset(out_dir, per_class=30, seed=42)

        splits = load_manifest(out_dir)
        results = train_and_evaluate(splits)

        n_classes = len(DocumentType)
        random_baseline = 1.0 / n_classes
        assert results["test_accuracy"] > random_baseline * 3
        assert results["n_train"] > 0
        assert results["n_test"] > 0
        assert set(results["labels"]) == {d.value for d in DocumentType}
        assert results["confusion_matrix"].shape == (n_classes, n_classes)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nall tests passed")
