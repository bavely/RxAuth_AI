"""Tests for pickle-free, self-describing model artifacts (roadmap Stage 2)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rxauth_ai.build_dataset import build_dataset
from rxauth_ai.classifier import DocumentClassifier, load_manifest, train_and_evaluate
from rxauth_ai.registry import (
    ARTIFACT_FORMAT_VERSION,
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    WEIGHTS_FILENAME,
    ArtifactError,
    fingerprint_training_data,
    load_classifier,
    save_classifier,
)


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("corpus")
    build_dataset(data_dir, per_class=20, seed=42)
    splits = load_manifest(data_dir)
    results = train_and_evaluate(splits)
    return results["classifier"], splits


def test_an_artifact_is_three_readable_files(trained, tmp_path: Path):
    classifier, _ = trained

    save_classifier(classifier, tmp_path / "model")

    assert sorted(item.name for item in (tmp_path / "model").iterdir()) == [
        MANIFEST_FILENAME,
        MODEL_FILENAME,
        WEIGHTS_FILENAME,
    ]
    # Readable without executing anything, which was the whole problem with pickle.
    json.loads((tmp_path / "model" / MODEL_FILENAME).read_text(encoding="utf-8"))
    json.loads((tmp_path / "model" / MANIFEST_FILENAME).read_text(encoding="utf-8"))


def test_a_round_trip_reproduces_predictions_exactly(trained, tmp_path: Path):
    """Bit-identical, not merely close: a reconstructed model is the same model."""
    classifier, splits = trained
    save_classifier(classifier, tmp_path / "model")

    restored = load_classifier(tmp_path / "model").classifier

    for text in splits["test"].texts:
        before = classifier.predict_text(text)
        after = restored.predict_text(text)
        assert after.label == before.label
        assert after.confidence == pytest.approx(before.confidence, abs=0.0)
        assert after.requires_human_review == before.requires_human_review


def test_the_manifest_records_what_trained_the_model(trained, tmp_path: Path):
    classifier, splits = trained
    train = splits["train"]

    manifest = save_classifier(
        classifier,
        tmp_path / "model",
        metrics={"test_macro_f1": 0.979},
        split_sizes={name: len(splits[name]) for name in sorted(splits)},
        training_data_fingerprint=fingerprint_training_data(
            list(train.case_ids), list(train.labels)
        ),
    )

    assert manifest.format_version == ARTIFACT_FORMAT_VERSION
    assert manifest.metrics["test_macro_f1"] == 0.979
    assert manifest.split_sizes["train"] == len(train)
    assert manifest.training_data_fingerprint
    assert manifest.sklearn_version and manifest.numpy_version and manifest.python_version


def test_the_same_training_data_fingerprints_the_same_and_a_relabel_does_not():
    ids = ["doc_a", "doc_b", "doc_c"]
    labels = ["lab_report", "clinical_note", "lab_report"]

    assert fingerprint_training_data(ids, labels) == fingerprint_training_data(ids, labels)
    # Order of presentation is not part of the training set's identity.
    assert fingerprint_training_data(ids, labels) == fingerprint_training_data(
        list(reversed(ids)), list(reversed(labels))
    )
    # A different label for the same document is a different training set.
    assert fingerprint_training_data(ids, labels) != fingerprint_training_data(
        ids, ["lab_report", "clinical_note", "referral"]
    )


def test_a_tampered_weights_file_is_refused(trained, tmp_path: Path):
    """The hash is the point: an artifact must be what its manifest says."""
    classifier, _ = trained
    save_classifier(classifier, tmp_path / "model")

    weights = tmp_path / "model" / WEIGHTS_FILENAME
    arrays = dict(np.load(weights))
    arrays["coef"] = arrays["coef"] * 2.0
    with weights.open("wb") as handle:
        np.savez(handle, **arrays)

    with pytest.raises(ArtifactError, match="does not match the SHA-256"):
        load_classifier(tmp_path / "model")


def test_a_tampered_model_file_is_refused(trained, tmp_path: Path):
    classifier, _ = trained
    save_classifier(classifier, tmp_path / "model")

    model_path = tmp_path / "model" / MODEL_FILENAME
    document = json.loads(model_path.read_text(encoding="utf-8"))
    document["confidence_threshold"] = 0.01
    model_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ArtifactError, match="does not match the SHA-256"):
        load_classifier(tmp_path / "model")


def test_an_artifact_from_a_future_format_is_refused_rather_than_guessed_at(
    trained, tmp_path: Path
):
    classifier, _ = trained
    save_classifier(classifier, tmp_path / "model")

    manifest_path = tmp_path / "model" / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format_version"] = "classifier-artifact-v99"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ArtifactError, match="Retrain rather than guessing"):
        load_classifier(tmp_path / "model")


def test_a_missing_artifact_names_the_commands_that_build_it(tmp_path: Path):
    with pytest.raises(ArtifactError, match="rxauth-train-classifier"):
        load_classifier(tmp_path / "never-trained")


def test_a_missing_weights_file_is_reported_rather_than_crashing(trained, tmp_path: Path):
    classifier, _ = trained
    save_classifier(classifier, tmp_path / "model")
    (tmp_path / "model" / WEIGHTS_FILENAME).unlink()

    with pytest.raises(ArtifactError, match="missing its weights file"):
        load_classifier(tmp_path / "model")


def test_a_scikit_learn_minor_version_change_warns_but_still_loads(trained, tmp_path: Path):
    """Refusing would be wrong; silence would be worse. See registry.py."""
    classifier, _ = trained
    save_classifier(classifier, tmp_path / "model")

    manifest_path = tmp_path / "model" / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sklearn_version"] = "0.1.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # The manifest hash covers model.json and weights.npz, not itself, so this
    # edit is legitimately loadable — which is what lets the warning be reached.
    with pytest.warns(RuntimeWarning, match="scikit-learn"):
        loaded = load_classifier(tmp_path / "model")

    assert loaded.classifier.predict_text("A1c: 7.4%").label


def test_the_classifier_save_load_pair_uses_the_registry(trained, tmp_path: Path):
    """`DocumentClassifier.save` no longer writes a pickle."""
    classifier, _ = trained

    classifier.save(tmp_path / "model")
    restored = DocumentClassifier.load(tmp_path / "model")

    assert (tmp_path / "model").is_dir()
    assert restored.predict_text("A1c: 7.4%").label == classifier.predict_text("A1c: 7.4%").label
