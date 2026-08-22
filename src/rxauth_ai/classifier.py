"""Document classification baseline — TF-IDF + Logistic Regression (main README §8, Phase 1).

Loads the reproducible synthetic dataset in `data/`, trains a TF-IDF +
Logistic Regression classifier on the train split, and evaluates it on a held-out
test split it never saw during fitting (val is reserved for future Phase-2
comparison / hyperparameter selection, not used here). Reports accuracy,
per-class precision/recall/F1, a confusion matrix, and inference latency —
the numbers `reports/classifier_baseline.md` is built from.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix


@dataclass
class DatasetSplit:
    texts: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.texts)


def load_manifest(data_dir: Path) -> dict[str, DatasetSplit]:
    manifest_path = data_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} not found — run `rxauth-build-dataset` first.")

    splits = {name: DatasetSplit() for name in ("train", "val", "test")}
    with manifest_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["split"] not in splits:
                raise ValueError(f"Unknown dataset split: {row['split']!r}")
            text = (data_dir / row["relative_path"]).read_text(encoding="utf-8")
            split = splits[row["split"]]
            split.texts.append(text)
            split.labels.append(row["label"])
            split.filenames.append(row["relative_path"])
    return splits


def train_and_evaluate(splits: dict[str, DatasetSplit]) -> dict[str, Any]:
    train, test = splits["train"], splits["test"]
    if not train or not test:
        raise ValueError("Both train and test splits must contain at least one document.")

    # Fit the vectorizer on train only — fitting on test/val would leak
    # test-set vocabulary statistics into training (main README §8 leakage prevention).
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

    train_pred = model.predict(x_train)
    train_accuracy = accuracy_score(train.labels, train_pred)

    start = time.perf_counter()
    test_pred = [model.predict(vectorizer.transform([text]))[0] for text in test.texts]
    elapsed = time.perf_counter() - start
    latency_ms_per_doc = (elapsed / len(test.texts)) * 1000 if test.texts else 0.0

    test_accuracy = accuracy_score(test.labels, test_pred)
    labels_sorted = sorted(set(train.labels) | set(test.labels))
    report_text = classification_report(
        test.labels, test_pred, labels=labels_sorted, digits=3, zero_division=0
    )
    matrix = confusion_matrix(test.labels, test_pred, labels=labels_sorted)

    misclassified = [
        (fn, true, pred, text[:90].replace("\n", " ").strip())
        for fn, true, pred, text in zip(
            test.filenames, test.labels, test_pred, test.texts, strict=True
        )
        if true != pred
    ]

    return {
        "vectorizer": vectorizer,
        "model": model,
        "labels": labels_sorted,
        "train_accuracy": train_accuracy,
        "test_accuracy": test_accuracy,
        "classification_report": report_text,
        "confusion_matrix": matrix,
        "latency_ms_per_doc": latency_ms_per_doc,
        "misclassified": misclassified,
        "n_train": len(train),
        "n_val": len(splits["val"]),
        "n_test": len(test),
    }


def render_report_md(results: dict[str, Any], data_dir: Path) -> str:
    labels = results["labels"]
    matrix = results["confusion_matrix"]
    gap = results["train_accuracy"] - results["test_accuracy"]

    lines = [
        "# Classifier baseline — TF-IDF + Logistic Regression",
        "",
        "_Main README §8, Phase 1. Reproducible: `rxauth-build-dataset` then "
        "`rxauth-train-classifier`._",
        "",
        "## Dataset",
        f"- Source: `{data_dir.name}/manifest.csv`",
        f"- Train / val / test sizes: {results['n_train']} / {results['n_val']} / {results['n_test']}",
        f"- Classes ({len(labels)}): {', '.join(labels)}",
        "- All documents are template-generated synthetic text (main README §3 guardrail) "
        "— no real patient, provider, or payer data.",
        "- The vectorizer is fit on the train split only; val/test text is never seen during "
        "fitting (leakage check).",
        "",
        "## Headline metrics",
        f"- Train accuracy: {results['train_accuracy']:.3f}",
        f"- Test accuracy: {results['test_accuracy']:.3f}",
        f"- Train/test accuracy gap: {gap:.3f} "
        + (
            "(no evidence of overfitting)" if gap < 0.05 else "(gap worth watching for overfitting)"
        ),
        f"- Inference latency: {results['latency_ms_per_doc']:.3f} ms/document "
        "(single-document predict, CPU, includes vectorization)",
        "",
        "## Per-class precision / recall / F1",
        "```",
        results["classification_report"].rstrip(),
        "```",
        "",
        "## Confusion matrix (rows = true label, columns = predicted label)",
        "| true \\ pred | " + " | ".join(labels) + " |",
        "|---" * (len(labels) + 1) + "|",
    ]
    for i, row_label in enumerate(labels):
        lines.append(f"| {row_label} | " + " | ".join(str(v) for v in matrix[i]) + " |")

    lines += ["", "## Failure cases"]
    if results["misclassified"]:
        lines += ["| file | true | predicted | text snippet |", "|---|---|---|---|"]
        for fn, true, pred, snippet in results["misclassified"][:20]:
            lines.append(f"| {fn} | {true} | {pred} | {snippet}... |")
    else:
        lines.append("None on this test split.")

    lines += [
        "",
        "## Known limitation",
        "This dataset is template-generated synthetic text, not real scanned/OCR'd documents. "
        "These numbers validate the pipeline and evaluation methodology end to end — they are "
        "not a claim about real-world generalization (main README §3, no-fabricated-metrics "
        "guardrail). Phase 2 (§8) compares a deep model against this same methodology on the "
        "same dataset contract.",
        "",
    ]
    return "\n".join(lines)
