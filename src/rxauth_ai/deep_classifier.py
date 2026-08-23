"""Small-transformer document classifier for README section 8, Phase 2.

The deep-learning dependencies are deliberately optional. Importing the core
package and running the classical baseline do not require PyTorch or
Transformers; this module loads them only when training or inference begins.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from .classifier import DatasetSplit, DocumentPrediction, _expected_calibration_error
from .ingestion import ingest_document
from .models import Document, DocumentType


class DeepLearningDependencyError(RuntimeError):
    """Raised when the optional Phase 2 dependencies are unavailable."""


@dataclass(frozen=True)
class DeepTrainingConfig:
    """Reproducible settings for the first transformer experiment."""

    model_name: str = "distilbert-base-uncased"
    epochs: int = 4
    batch_size: int = 16
    learning_rate: float = 2e-5
    weight_decay: float = 0.01
    max_length: int = 256
    seed: int = 42
    early_stopping_patience: int = 2
    device: str = "auto"

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("model_name must not be empty.")
        if self.epochs < 1:
            raise ValueError("epochs must be at least 1.")
        if self.batch_size < 1:
            raise ValueError("batch_size must be at least 1.")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive.")
        if self.weight_decay < 0:
            raise ValueError("weight_decay must not be negative.")
        if self.max_length < 8:
            raise ValueError("max_length must be at least 8 tokens.")
        if self.early_stopping_patience < 1:
            raise ValueError("early_stopping_patience must be at least 1.")
        if self.device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError("device must be one of: auto, cpu, cuda, mps.")


@dataclass(frozen=True)
class _DeepDependencies:
    torch: Any
    auto_tokenizer: Any
    auto_model: Any


def _load_deep_dependencies() -> _DeepDependencies:
    try:
        torch = import_module("torch")
        transformers = import_module("transformers")
    except ImportError as exc:
        raise DeepLearningDependencyError(
            "Phase 2 requires the optional deep-learning dependencies. "
            "Install them with `uv sync --extra deep --group dev`."
        ) from exc
    return _DeepDependencies(
        torch=torch,
        auto_tokenizer=transformers.AutoTokenizer,
        auto_model=transformers.AutoModelForSequenceClassification,
    )


def _resolve_device(torch: Any, requested: str) -> str:
    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        return "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but is not available.")
    if requested == "mps":
        mps = getattr(torch.backends, "mps", None)
        if mps is None or not mps.is_available():
            raise ValueError("MPS was requested but is not available.")
    return requested


def _seed_everything(torch: Any, seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch, "use_deterministic_algorithms"):
        torch.use_deterministic_algorithms(True, warn_only=True)


class _EncodedDataset:
    def __init__(self, encoded: dict[str, Any], label_ids: Any) -> None:
        self.encoded = encoded
        self.label_ids = label_ids

    def __len__(self) -> int:
        return len(self.label_ids)

    def __getitem__(self, index: int) -> dict[str, Any]:
        item = {key: values[index] for key, values in self.encoded.items()}
        item["labels"] = self.label_ids[index]
        return item


@dataclass
class DeepDocumentClassifier:
    """Inference wrapper around a fine-tuned Hugging Face sequence classifier."""

    tokenizer: Any
    model: Any
    labels: list[str]
    model_name: str
    device: str = "cpu"
    max_length: int = 256
    confidence_threshold: float = 0.65

    def predict_text(self, text: str) -> DocumentPrediction:
        deps = _load_deep_dependencies()
        encoded = self.tokenizer(
            [text],
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        self.model.eval()
        with deps.torch.inference_mode():
            logits = self.model(**encoded).logits
            probabilities = deps.torch.softmax(logits, dim=-1)[0]
        best_index = int(deps.torch.argmax(probabilities).item())
        confidence = float(probabilities[best_index].item())
        return DocumentPrediction(
            label=self.labels[best_index],
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
        path.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(path)
        self.tokenizer.save_pretrained(path)
        metadata = {
            "artifact_format": 1,
            "labels": self.labels,
            "model_name": self.model_name,
            "max_length": self.max_length,
            "confidence_threshold": self.confidence_threshold,
        }
        (path / "rxauth_metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    @classmethod
    def load(cls, path: Path, *, device: str = "auto") -> DeepDocumentClassifier:
        deps = _load_deep_dependencies()
        metadata_path = path / "rxauth_metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Missing deep-classifier metadata: {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("artifact_format") != 1:
            raise ValueError("Unsupported deep-classifier artifact format.")
        resolved_device = _resolve_device(deps.torch, device)
        tokenizer = deps.auto_tokenizer.from_pretrained(path)
        model = deps.auto_model.from_pretrained(path)
        model.to(resolved_device)
        return cls(
            tokenizer=tokenizer,
            model=model,
            labels=list(metadata["labels"]),
            model_name=str(metadata["model_name"]),
            device=resolved_device,
            max_length=int(metadata["max_length"]),
            confidence_threshold=float(metadata["confidence_threshold"]),
        )


def _make_dataset(
    split: DatasetSplit,
    *,
    tokenizer: Any,
    label_to_id: dict[str, int],
    max_length: int,
    torch: Any,
) -> _EncodedDataset:
    encoded = tokenizer(
        split.texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    label_ids = torch.tensor([label_to_id[label] for label in split.labels], dtype=torch.long)
    return _EncodedDataset(dict(encoded), label_ids)


def _evaluate_split(
    classifier: DeepDocumentClassifier,
    split: DatasetSplit,
    labels: list[str],
    *,
    batch_size: int,
    torch: Any,
) -> dict[str, Any]:
    label_to_id = {label: index for index, label in enumerate(labels)}
    dataset = _make_dataset(
        split,
        tokenizer=classifier.tokenizer,
        label_to_id=label_to_id,
        max_length=classifier.max_length,
        torch=torch,
    )
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=False)
    probabilities: list[Any] = []
    classifier.model.eval()
    start = time.perf_counter()
    with torch.inference_mode():
        for batch in loader:
            inputs = {
                key: value.to(classifier.device) for key, value in batch.items() if key != "labels"
            }
            batch_probabilities = torch.softmax(classifier.model(**inputs).logits, dim=-1)
            probabilities.append(batch_probabilities.cpu())
    elapsed = time.perf_counter() - start

    probability_matrix = torch.cat(probabilities).numpy()
    prediction_indices = np.argmax(probability_matrix, axis=1)
    predictions = [labels[index] for index in prediction_indices]
    confidences = probability_matrix[np.arange(len(predictions)), prediction_indices]
    return {
        "accuracy": accuracy_score(split.labels, predictions),
        "macro_f1": f1_score(
            split.labels, predictions, labels=labels, average="macro", zero_division=0
        ),
        "classification_report": classification_report(
            split.labels, predictions, labels=labels, digits=3, zero_division=0
        ),
        "confusion_matrix": confusion_matrix(split.labels, predictions, labels=labels),
        "latency_ms_per_doc": (elapsed / len(split)) * 1000,
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


def train_and_evaluate_deep(
    splits: dict[str, DatasetSplit],
    *,
    config: DeepTrainingConfig | None = None,
    confidence_threshold: float = 0.65,
) -> dict[str, Any]:
    """Fine-tune a transformer and evaluate without selecting on test/challenge data."""
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be between 0 and 1.")
    config = config or DeepTrainingConfig()
    deps = _load_deep_dependencies()
    torch = deps.torch
    _seed_everything(torch, config.seed)
    device = _resolve_device(torch, config.device)

    labels = sorted({label for split in splits.values() for label in split.labels})
    label_to_id = {label: index for index, label in enumerate(labels)}
    id_to_label = {index: label for label, index in label_to_id.items()}
    tokenizer = deps.auto_tokenizer.from_pretrained(config.model_name)
    model = deps.auto_model.from_pretrained(
        config.model_name,
        num_labels=len(labels),
        label2id=label_to_id,
        id2label=id_to_label,
        ignore_mismatched_sizes=True,
    )
    model.to(device)
    classifier = DeepDocumentClassifier(
        tokenizer=tokenizer,
        model=model,
        labels=labels,
        model_name=config.model_name,
        device=device,
        max_length=config.max_length,
        confidence_threshold=confidence_threshold,
    )

    train_dataset = _make_dataset(
        splits["train"],
        tokenizer=tokenizer,
        label_to_id=label_to_id,
        max_length=config.max_length,
        torch=torch,
    )
    generator = torch.Generator()
    generator.manual_seed(config.seed)
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )

    history: list[dict[str, float | int]] = []
    best_state: dict[str, Any] | None = None
    best_val_f1 = -1.0
    best_epoch = 0
    epochs_without_improvement = 0
    training_start = time.perf_counter()
    for epoch in range(1, config.epochs + 1):
        model.train()
        total_loss = 0.0
        for batch in train_loader:
            optimizer.zero_grad(set_to_none=True)
            batch = {key: value.to(device) for key, value in batch.items()}
            output = model(**batch)
            output.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += float(output.loss.detach().cpu().item())

        validation = _evaluate_split(
            classifier,
            splits["val"],
            labels,
            batch_size=config.batch_size,
            torch=torch,
        )
        mean_loss = total_loss / len(train_loader)
        history.append(
            {"epoch": epoch, "train_loss": mean_loss, "val_macro_f1": validation["macro_f1"]}
        )
        if validation["macro_f1"] > best_val_f1:
            best_val_f1 = validation["macro_f1"]
            best_epoch = epoch
            best_state = {
                name: parameter.detach().cpu().clone()
                for name, parameter in model.state_dict().items()
            }
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= config.early_stopping_patience:
                break

    training_seconds = time.perf_counter() - training_start
    if best_state is None:
        raise RuntimeError("Training completed without producing a checkpoint.")
    model.load_state_dict(best_state)
    model.to(device)

    evaluations = {
        name: _evaluate_split(
            classifier,
            splits[name],
            labels,
            batch_size=config.batch_size,
            torch=torch,
        )
        for name in ("val", "test", "challenge")
    }
    train_evaluation = _evaluate_split(
        classifier,
        splits["train"],
        labels,
        batch_size=config.batch_size,
        torch=torch,
    )
    return {
        "classifier": classifier,
        "labels": labels,
        "config": config,
        "device": device,
        "history": history,
        "best_epoch": best_epoch,
        "training_seconds": training_seconds,
        "train_accuracy": train_evaluation["accuracy"],
        "evaluations": evaluations,
        **{f"n_{name}": len(split) for name, split in splits.items()},
    }


def artifact_size_mb(path: Path) -> float:
    """Return the recursive size of a model file or directory in MiB."""
    if path.is_file():
        size = path.stat().st_size
    elif path.is_dir():
        size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    else:
        raise FileNotFoundError(path)
    return size / (1024 * 1024)


def render_comparison_report(
    baseline: dict[str, Any],
    deep: dict[str, Any],
    *,
    data_dir: Path,
    baseline_artifact_mb: float,
    deep_artifact_mb: float,
) -> str:
    """Render the Phase 2 report from metrics produced by one paired run."""
    labels = deep["labels"]
    config: DeepTrainingConfig = deep["config"]
    lines = [
        "# Phase 2 classifier comparison — transformer vs classical baseline",
        "",
        "_Reproducible: `uv sync --extra deep --group dev`, then "
        "`uv run rxauth-train-deep-classifier`._",
        "",
        "## Experiment contract",
        f"- Source: `{data_dir.name}/manifest.csv`",
        (
            "- Train / val / test / challenge sizes: "
            f"{deep['n_train']} / {deep['n_val']} / {deep['n_test']} / "
            f"{deep['n_challenge']}"
        ),
        "- Both models use the same case- and template-family-isolated splits.",
        "- Transformer checkpoint selection and early stopping use validation macro F1 only.",
        "- Test is the held-out comparison; challenge is robustness analysis only.",
        "- All content is synthetic; results do not establish clinical or production validity.",
        "",
        "## Training configuration",
        f"- Pretrained model: `{config.model_name}`",
        f"- Seed: {config.seed}",
        f"- Device: {deep['device']}",
        f"- Epochs completed / selected: {len(deep['history'])} / {deep['best_epoch']}",
        f"- Batch size / max tokens: {config.batch_size} / {config.max_length}",
        f"- Learning rate / weight decay: {config.learning_rate:g} / {config.weight_decay:g}",
        f"- Fine-tuning time: {deep['training_seconds']:.1f} seconds",
        "",
        "## Paired results",
        "| Split | Model | Accuracy | Macro F1 | ECE | Review rate | Model latency (ms/doc) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for split_name in ("val", "test", "challenge"):
        for model_name, results in (("TF-IDF + LogReg", baseline), ("Transformer", deep)):
            evaluation = results["evaluations"][split_name]
            lines.append(
                f"| {split_name} | {model_name} | {evaluation['accuracy']:.3f} | "
                f"{evaluation['macro_f1']:.3f} | "
                f"{evaluation['expected_calibration_error']:.3f} | "
                f"{evaluation['review_rate']:.1%} | "
                f"{evaluation['latency_ms_per_doc']:.3f} |"
            )
    lines += [
        "",
        "## Deployment cost",
        "| Model | Artifact size | Inference device |",
        "|---|---:|---|",
        f"| TF-IDF + LogReg | {baseline_artifact_mb:.2f} MiB | CPU |",
        f"| Transformer | {deep_artifact_mb:.2f} MiB | {deep['device']} |",
        "",
        "Latency measures model execution after vectorization/tokenization and should be compared "
        "only on the same machine. Artifact size includes tokenizer/config files for the transformer.",
        "",
        "## Transformer training history",
        "| Epoch | Train loss | Validation macro F1 |",
        "|---:|---:|---:|",
    ]
    for row in deep["history"]:
        lines.append(f"| {row['epoch']} | {row['train_loss']:.4f} | {row['val_macro_f1']:.3f} |")

    test = deep["evaluations"]["test"]
    lines += [
        "",
        "## Transformer test classification report",
        "```",
        test["classification_report"].rstrip(),
        "```",
        "",
        "## Transformer test confusion matrix (rows = true, columns = predicted)",
        "| true \\ pred | " + " | ".join(labels) + " |",
        "|---" * (len(labels) + 1) + "|",
    ]
    for index, row_label in enumerate(labels):
        lines.append(
            f"| {row_label} | "
            + " | ".join(str(value) for value in test["confusion_matrix"][index])
            + " |"
        )

    lines += ["", "## Transformer challenge failure cases"]
    failures = deep["evaluations"]["challenge"]["misclassified"]
    if failures:
        lines += ["| file | true | predicted | text snippet |", "|---|---|---|---|"]
        for filename, truth, prediction, snippet in failures[:20]:
            lines.append(f"| {filename} | {truth} | {prediction} | {snippet}... |")
    else:
        lines.append("None on this challenge split.")

    test_delta = (
        deep["evaluations"]["test"]["macro_f1"] - baseline["evaluations"]["test"]["macro_f1"]
    )
    lines += [
        "",
        "## Interpretation",
        f"The transformer test macro-F1 delta versus the baseline is {test_delta:+.3f} in this "
        "single seeded run. Model choice must also account for calibration, review routing, "
        "challenge robustness, latency, and artifact size. Repeat-seed variance and error review "
        "are required before promoting either model.",
        "",
    ]
    return "\n".join(lines)
