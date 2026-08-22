"""Leakage-resistant TF-IDF classifier benchmark for Phase 1.5."""

from __future__ import annotations

import csv
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from .ingestion import ingest_document
from .models import Document, DocumentType


@dataclass
class DatasetSplit:
    texts: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    case_ids: list[str] = field(default_factory=list)
    template_family_ids: list[str] = field(default_factory=list)
    degradations: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.texts)


@dataclass(frozen=True)
class DocumentPrediction:
    label: str
    confidence: float
    requires_human_review: bool


@dataclass
class DocumentClassifier:
    vectorizer: TfidfVectorizer
    model: LogisticRegression
    confidence_threshold: float = 0.65

    def predict_text(self, text: str) -> DocumentPrediction:
        probabilities = self.model.predict_proba(self.vectorizer.transform([text]))[0]
        best_index = int(np.argmax(probabilities))
        confidence = float(probabilities[best_index])
        return DocumentPrediction(
            label=str(self.model.classes_[best_index]),
            confidence=confidence,
            requires_human_review=confidence < self.confidence_threshold,
        )

    def classify_path(self, path: Path, *, document_id: str) -> tuple[Document, bool]:
        ingested = ingest_document(path)
        prediction = self.predict_text(ingested.text)
        document = Document(
            id=document_id,
            filename=path.name,
            document_type=DocumentType(prediction.label),
            classification_confidence=prediction.confidence,
            page_count=len(ingested.pages),
        )
        return document, prediction.requires_human_review

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump(self, handle)

    @classmethod
    def load(cls, path: Path) -> DocumentClassifier:
        # Pickle artifacts are executable data and must only be loaded from a trusted build.
        with path.open("rb") as handle:
            loaded = pickle.load(handle)  # noqa: S301
        if not isinstance(loaded, cls):
            raise TypeError(f"Artifact at {path} is not a {cls.__name__}.")
        return loaded


def validate_split_isolation(splits: dict[str, DatasetSplit]) -> None:
    """Reject case or template-family leakage between benchmark partitions."""
    names = list(splits)
    for index, left_name in enumerate(names):
        left = splits[left_name]
        for right_name in names[index + 1 :]:
            right = splits[right_name]
            shared_cases = set(left.case_ids) & set(right.case_ids)
            shared_families = set(left.template_family_ids) & set(right.template_family_ids)
            if shared_cases or shared_families:
                details = []
                if shared_cases:
                    details.append(f"case IDs {sorted(shared_cases)[:3]}")
                if shared_families:
                    details.append(f"template families {sorted(shared_families)[:3]}")
                raise ValueError(
                    f"Dataset leakage between {left_name!r} and {right_name!r}: "
                    + ", ".join(details)
                )


def load_manifest(data_dir: Path) -> dict[str, DatasetSplit]:
    manifest_path = data_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} not found — run `rxauth-build-dataset` first.")

    split_names = ("train", "val", "test", "challenge")
    splits = {name: DatasetSplit() for name in split_names}
    with manifest_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {
            "relative_path",
            "label",
            "split",
            "case_id",
            "template_family_id",
            "degradation",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Manifest uses the pre-Phase-1.5 contract; rebuild it. Missing fields: "
                + ", ".join(sorted(missing))
            )
        for row in reader:
            if row["split"] not in splits:
                raise ValueError(f"Unknown dataset split: {row['split']!r}")
            path = data_dir / row["relative_path"]
            text = ingest_document(path).text
            split = splits[row["split"]]
            split.texts.append(text)
            split.labels.append(row["label"])
            split.filenames.append(row["relative_path"])
            split.case_ids.append(row["case_id"])
            split.template_family_ids.append(row["template_family_id"])
            split.degradations.append(row["degradation"])

    if any(not split for split in splits.values()):
        empty = [name for name, split in splits.items() if not split]
        raise ValueError(f"Dataset splits must be non-empty: {', '.join(empty)}")
    validate_split_isolation(splits)
    return splits


def _expected_calibration_error(
    labels: list[str], predictions: list[str], confidences: np.ndarray, bins: int = 10
) -> float:
    correctness = np.asarray(
        [truth == prediction for truth, prediction in zip(labels, predictions, strict=True)]
    )
    edges = np.linspace(0.0, 1.0, bins + 1)
    error = 0.0
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        selected = (confidences > lower) & (confidences <= upper)
        if not np.any(selected):
            continue
        bin_accuracy = float(np.mean(correctness[selected]))
        bin_confidence = float(np.mean(confidences[selected]))
        error += float(np.mean(selected)) * abs(bin_accuracy - bin_confidence)
    return error


def _evaluate_split(
    classifier: DocumentClassifier, split: DatasetSplit, labels: list[str]
) -> dict[str, Any]:
    matrix = classifier.vectorizer.transform(split.texts)
    start = time.perf_counter()
    probabilities = classifier.model.predict_proba(matrix)
    elapsed = time.perf_counter() - start
    prediction_indices = np.argmax(probabilities, axis=1)
    predictions = [str(classifier.model.classes_[index]) for index in prediction_indices]
    confidences = probabilities[np.arange(len(predictions)), prediction_indices]
    latency = (elapsed / len(split)) * 1000

    return {
        "accuracy": accuracy_score(split.labels, predictions),
        "macro_f1": f1_score(
            split.labels, predictions, labels=labels, average="macro", zero_division=0
        ),
        "classification_report": classification_report(
            split.labels, predictions, labels=labels, digits=3, zero_division=0
        ),
        "confusion_matrix": confusion_matrix(split.labels, predictions, labels=labels),
        "latency_ms_per_doc": latency,
        "mean_confidence": float(np.mean(confidences)),
        "expected_calibration_error": _expected_calibration_error(
            split.labels, predictions, confidences
        ),
        "review_rate": float(np.mean(confidences < classifier.confidence_threshold)),
        "misclassified": [
            (filename, truth, prediction, text[:90].replace("\n", " ").strip())
            for filename, truth, prediction, text in zip(
                split.filenames, split.labels, predictions, split.texts, strict=True
            )
            if truth != prediction
        ],
    }


def train_and_evaluate(
    splits: dict[str, DatasetSplit], *, confidence_threshold: float = 0.65
) -> dict[str, Any]:
    train = splits["train"]
    labels = sorted({label for split in splits.values() for label in split.labels})
    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2,
        sublinear_tf=True,
    )
    x_train = vectorizer.fit_transform(train.texts)
    model = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)
    model.fit(x_train, train.labels)
    classifier = DocumentClassifier(vectorizer, model, confidence_threshold)

    train_predictions = model.predict(x_train)
    train_accuracy = accuracy_score(train.labels, train_predictions)
    evaluations = {
        name: _evaluate_split(classifier, splits[name], labels)
        for name in ("val", "test", "challenge")
    }
    test = evaluations["test"]

    return {
        "classifier": classifier,
        "vectorizer": vectorizer,
        "model": model,
        "labels": labels,
        "train_accuracy": train_accuracy,
        "evaluations": evaluations,
        "test_accuracy": test["accuracy"],
        "classification_report": test["classification_report"],
        "confusion_matrix": test["confusion_matrix"],
        "latency_ms_per_doc": test["latency_ms_per_doc"],
        "misclassified": test["misclassified"],
        **{f"n_{name}": len(split) for name, split in splits.items()},
    }


def _render_evaluation(lines: list[str], name: str, evaluation: dict[str, Any]) -> None:
    lines += [
        f"## {name.capitalize()} metrics",
        f"- Accuracy: {evaluation['accuracy']:.3f}",
        f"- Macro F1: {evaluation['macro_f1']:.3f}",
        f"- Mean confidence: {evaluation['mean_confidence']:.3f}",
        f"- Expected calibration error (10 bins): {evaluation['expected_calibration_error']:.3f}",
        f"- Human-review routing rate: {evaluation['review_rate']:.1%}",
        f"- Batch inference latency: {evaluation['latency_ms_per_doc']:.3f} ms/document (CPU)",
        "",
        "```",
        evaluation["classification_report"].rstrip(),
        "```",
        "",
    ]


def render_report_md(results: dict[str, Any], data_dir: Path) -> str:
    labels = results["labels"]
    test = results["evaluations"]["test"]
    lines = [
        "# Phase 1.5 classifier benchmark — TF-IDF + Logistic Regression",
        "",
        "_Reproducible: `rxauth-build-dataset` then `rxauth-train-classifier`._",
        "",
        "## Dataset contract",
        f"- Source: `{data_dir.name}/manifest.csv`",
        (
            "- Train / val / test / challenge sizes: "
            f"{results['n_train']} / {results['n_val']} / {results['n_test']} / "
            f"{results['n_challenge']}"
        ),
        f"- Classes ({len(labels)}): {', '.join(labels)}",
        "- Cases and template families are mutually exclusive across every split.",
        "- Challenge documents use an unseen layout family, cross-class noise, and "
        "deterministic OCR-like character corruption.",
        "- All content is synthetic; these metrics do not claim clinical or production validity.",
        "",
        "## Training",
        f"- Train accuracy: {results['train_accuracy']:.3f}",
        "- Vectorizer fit: train split only",
        "- Confidence threshold for human review: "
        f"{results['classifier'].confidence_threshold:.2f}",
        "",
    ]
    for name in ("val", "test", "challenge"):
        _render_evaluation(lines, name, results["evaluations"][name])

    lines += [
        "## Test confusion matrix (rows = true label, columns = predicted label)",
        "| true \\ pred | " + " | ".join(labels) + " |",
        "|---" * (len(labels) + 1) + "|",
    ]
    for index, row_label in enumerate(labels):
        lines.append(
            f"| {row_label} | "
            + " | ".join(str(value) for value in test["confusion_matrix"][index])
            + " |"
        )

    lines += ["", "## Challenge failure cases"]
    failures = results["evaluations"]["challenge"]["misclassified"]
    if failures:
        lines += ["| file | true | predicted | text snippet |", "|---|---|---|---|"]
        for filename, truth, prediction, snippet in failures[:20]:
            lines.append(f"| {filename} | {truth} | {prediction} | {snippet}... |")
    else:
        lines.append("None on this challenge split.")

    lines += [
        "",
        "## Interpretation",
        "The grouped test set is the primary Phase 1.5 comparison point. The challenge set "
        "is deliberately harder and should be used for robustness/error analysis, not model "
        "selection. The rendered PDF/image corpus separately validates the ingestion boundary; "
        "image OCR quality depends on the configured OCR backend.",
        "",
    ]
    return "\n".join(lines)
