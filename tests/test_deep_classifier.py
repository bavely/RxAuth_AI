"""Dependency-light tests for the Phase 2 transformer experiment harness."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rxauth_ai import deep_classifier
from rxauth_ai.deep_classifier import (
    DeepLearningDependencyError,
    DeepTrainingConfig,
    artifact_size_mb,
    render_comparison_report,
)


def test_deep_configuration_rejects_invalid_experiments():
    with pytest.raises(ValueError, match="epochs"):
        DeepTrainingConfig(epochs=0)
    with pytest.raises(ValueError, match="device"):
        DeepTrainingConfig(device="quantum")


def test_missing_deep_dependencies_have_an_actionable_message(monkeypatch):
    def missing_import(_name: str):
        raise ImportError("not installed")

    monkeypatch.setattr(deep_classifier, "import_module", missing_import)
    with pytest.raises(DeepLearningDependencyError, match="uv sync --extra deep"):
        deep_classifier._load_deep_dependencies()


def test_artifact_size_supports_file_and_directory(tmp_path: Path):
    model_file = tmp_path / "baseline.bin"
    model_file.write_bytes(b"a" * 1024)
    model_dir = tmp_path / "deep"
    model_dir.mkdir()
    (model_dir / "weights.bin").write_bytes(b"b" * 2048)

    assert artifact_size_mb(model_file) == pytest.approx(1 / 1024)
    assert artifact_size_mb(model_dir) == pytest.approx(2 / 1024)


def _evaluation(value: float) -> dict:
    return {
        "accuracy": value,
        "macro_f1": value,
        "classification_report": "precision recall f1-score support",
        "confusion_matrix": np.eye(2, dtype=int),
        "latency_ms_per_doc": value,
        "expected_calibration_error": 1 - value,
        "review_rate": 0.25,
        "misclassified": [],
    }


def test_comparison_report_records_protocol_and_tradeoffs():
    baseline = {"evaluations": {name: _evaluation(0.7) for name in ("val", "test", "challenge")}}
    deep = {
        "labels": ["clinical_note", "other"],
        "config": DeepTrainingConfig(epochs=1),
        "device": "cpu",
        "history": [{"epoch": 1, "train_loss": 0.5, "val_macro_f1": 0.8}],
        "best_epoch": 1,
        "training_seconds": 3.0,
        "n_train": 20,
        "n_val": 4,
        "n_test": 4,
        "n_challenge": 4,
        "evaluations": {name: _evaluation(0.8) for name in ("val", "test", "challenge")},
    }

    report = render_comparison_report(
        baseline,
        deep,
        data_dir=Path("data"),
        baseline_artifact_mb=0.2,
        deep_artifact_mb=250.0,
    )

    assert "validation macro F1 only" in report
    assert "TF-IDF + LogReg" in report
    assert "Transformer" in report
    assert "Transformer test failure cases" in report
    assert "single seeded run" in report
    assert "+0.100" in report


def test_comparison_report_aggregates_repeat_seeds():
    baseline = {"evaluations": {name: _evaluation(0.7) for name in ("val", "test", "challenge")}}
    first = {
        "labels": ["clinical_note", "other"],
        "config": DeepTrainingConfig(epochs=1, seed=7),
        "device": "cpu",
        "hardware": "test CPU",
        "history": [{"epoch": 1, "train_loss": 0.5, "val_macro_f1": 0.8}],
        "best_epoch": 1,
        "training_seconds": 3.0,
        "n_train": 20,
        "n_val": 4,
        "n_test": 4,
        "n_challenge": 4,
        "evaluations": {name: _evaluation(0.8) for name in ("val", "test", "challenge")},
    }
    second = {
        **first,
        "config": DeepTrainingConfig(epochs=1, seed=42),
        "evaluations": {name: _evaluation(0.9) for name in ("val", "test", "challenge")},
    }

    report = render_comparison_report(
        baseline,
        second,
        data_dir=Path("data"),
        baseline_artifact_mb=0.2,
        deep_artifact_mb=250.0,
        deep_runs=[first, second],
    )

    assert "Seeds run: 7, 42" in report
    assert "Repeat-seed summary" in report
    assert "0.850 ± 0.071" in report
    assert "Across 2 seeded runs" in report
