"""Versioned model artifacts without pickle (roadmap Stage 2).

`DocumentClassifier.save` used `pickle.dump(self)`. That has three problems a
deployed system cannot carry:

1. **A pickle is executable data.** Loading one runs whatever it contains. The
   old code carried `# noqa: S301` acknowledging exactly that, with no answer.
2. **It is not portable across library versions.** A pickled scikit-learn
   estimator is only reliably loadable by the version that wrote it, so a
   dependency bump can silently invalidate an artifact — or, worse, load one
   that behaves differently.
3. **It records nothing about itself.** There is no way to ask a `.pkl` what
   data trained it, what it scored, or which library built it, so "is the model
   in production the one the report describes?" is unanswerable.

So an artifact is a directory: `model.json` holds the vocabulary and
hyperparameters, `weights.npz` holds the float arrays, and `manifest.json`
records what it is, what trained it, what it scored, and the SHA-256 of the two
data files. Loading verifies the hashes and refuses on a mismatch.

**On library versions.** The manifest records scikit-learn, numpy, and Python
versions, and loading a model built by a different scikit-learn *minor* version
warns rather than refuses. Refusing would be wrong: the reconstruction below
sets documented fitted attributes and does not depend on internal layout, so it
survives ordinary upgrades. Silence would also be wrong, because the day it
stops surviving one, the warning is the only thread to pull.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from .classifier import DocumentClassifier

ARTIFACT_FORMAT_VERSION = "classifier-artifact-v1"

MODEL_FILENAME = "model.json"
WEIGHTS_FILENAME = "weights.npz"
MANIFEST_FILENAME = "manifest.json"

#: Vectorizer hyperparameters that change what a feature *means*. Persisted so
#: a reconstructed vectorizer tokenizes identically; anything absent here would
#: silently produce a different feature space for the same text.
_VECTORIZER_PARAMS = ("lowercase", "stop_words", "ngram_range", "min_df", "sublinear_tf")


class ArtifactError(RuntimeError):
    """Raised when an artifact cannot be trusted to be what it claims."""


class ModelManifest(BaseModel):
    """What this artifact is, what made it, and what it scored."""

    format_version: str = ARTIFACT_FORMAT_VERSION
    model_type: str = "tfidf-logreg"
    created_at: str

    confidence_threshold: float = Field(ge=0.0, le=1.0)
    classes: list[str]
    n_features: int

    training_data_fingerprint: Optional[str] = Field(
        default=None, description="SHA-256 over the training split's document IDs and labels."
    )
    split_sizes: dict[str, int] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)

    python_version: str
    numpy_version: str
    sklearn_version: str

    model_sha256: str
    weights_sha256: str


@dataclass(frozen=True)
class LoadedModel:
    """A reconstructed classifier and the manifest that describes it."""

    classifier: DocumentClassifier
    manifest: ModelManifest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint_training_data(document_ids: list[str], labels: list[str]) -> str:
    """A stable hash of exactly what the model was shown.

    Ordered pairs, because a different assignment of the same documents to the
    same labels is a different training set.
    """
    payload = json.dumps(sorted(zip(document_ids, labels, strict=True)), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_classifier(
    classifier: DocumentClassifier,
    path: Path,
    *,
    metrics: Optional[dict[str, float]] = None,
    split_sizes: Optional[dict[str, int]] = None,
    training_data_fingerprint: Optional[str] = None,
) -> ModelManifest:
    """Write a self-describing, pickle-free artifact directory."""
    import sklearn

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)

    vectorizer = classifier.vectorizer
    model = classifier.model

    params = vectorizer.get_params()
    model_document: dict[str, Any] = {
        "format_version": ARTIFACT_FORMAT_VERSION,
        "vectorizer": {
            "params": {
                name: (list(params[name]) if isinstance(params[name], tuple) else params[name])
                for name in _VECTORIZER_PARAMS
            },
            "vocabulary": {term: int(index) for term, index in vectorizer.vocabulary_.items()},
        },
        "model": {
            "classes": [str(label) for label in model.classes_],
            "n_features_in": int(model.n_features_in_),
        },
        "confidence_threshold": float(classifier.confidence_threshold),
    }

    model_path = path / MODEL_FILENAME
    model_path.write_text(
        json.dumps(model_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    weights_path = path / WEIGHTS_FILENAME
    with weights_path.open("wb") as handle:
        np.savez(
            handle,
            idf=np.asarray(vectorizer.idf_, dtype=np.float64),
            coef=np.asarray(model.coef_, dtype=np.float64),
            intercept=np.asarray(model.intercept_, dtype=np.float64),
        )

    manifest = ModelManifest(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        confidence_threshold=float(classifier.confidence_threshold),
        classes=[str(label) for label in model.classes_],
        n_features=int(model.n_features_in_),
        training_data_fingerprint=training_data_fingerprint,
        split_sizes=split_sizes or {},
        metrics=metrics or {},
        python_version=platform.python_version(),
        numpy_version=np.__version__,
        sklearn_version=sklearn.__version__,
        model_sha256=_sha256(model_path),
        weights_sha256=_sha256(weights_path),
    )
    (path / MANIFEST_FILENAME).write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def load_manifest(path: Path) -> ModelManifest:
    manifest_path = Path(path) / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ArtifactError(
            f"No model manifest at {manifest_path}. Build the artifact first:\n"
            "    uv run rxauth-build-dataset\n"
            "    uv run rxauth-train-classifier"
        )
    try:
        return ModelManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ArtifactError(f"Model manifest at {manifest_path} is unreadable: {exc}") from exc


def load_classifier(path: Path, *, verify: bool = True) -> LoadedModel:
    """Reconstruct the classifier, refusing an artifact that is not intact.

    `verify=False` exists for the one legitimate case — inspecting an artifact
    you already know is damaged — and is not what production should call.
    """
    from .classifier import DocumentClassifier

    path = Path(path)
    manifest = load_manifest(path)

    if manifest.format_version != ARTIFACT_FORMAT_VERSION:
        raise ArtifactError(
            f"Artifact at {path} is format {manifest.format_version!r}; this build reads "
            f"{ARTIFACT_FORMAT_VERSION!r}. Retrain rather than guessing at the difference."
        )

    model_path = path / MODEL_FILENAME
    weights_path = path / WEIGHTS_FILENAME
    for name, file_path, expected in (
        ("model", model_path, manifest.model_sha256),
        ("weights", weights_path, manifest.weights_sha256),
    ):
        if not file_path.is_file():
            raise ArtifactError(f"Artifact at {path} is missing its {name} file.")
        if verify and _sha256(file_path) != expected:
            raise ArtifactError(
                f"{file_path} does not match the SHA-256 its manifest records. The artifact has "
                "been modified or truncated; retrain rather than loading it."
            )

    _warn_on_library_drift(manifest)

    document = json.loads(model_path.read_text(encoding="utf-8"))
    arrays = np.load(weights_path)

    vectorizer = _rebuild_vectorizer(document["vectorizer"], arrays["idf"])
    model = _rebuild_model(document["model"], arrays["coef"], arrays["intercept"])
    classifier = DocumentClassifier(vectorizer, model, float(document["confidence_threshold"]))
    return LoadedModel(classifier=classifier, manifest=manifest)


def _warn_on_library_drift(manifest: ModelManifest) -> None:
    import warnings

    import sklearn

    built, running = manifest.sklearn_version, sklearn.__version__
    if built.split(".")[:2] != running.split(".")[:2]:
        warnings.warn(
            f"Artifact was built with scikit-learn {built}; this process has {running}. "
            "Reconstruction uses documented fitted attributes and should hold, but if "
            "predictions look wrong this is the first thing to check.",
            RuntimeWarning,
            stacklevel=3,
        )


def _rebuild_vectorizer(document: dict[str, Any], idf: np.ndarray) -> Any:
    from sklearn.feature_extraction.text import TfidfTransformer, TfidfVectorizer

    params = dict(document["params"])
    if isinstance(params.get("ngram_range"), list):
        params["ngram_range"] = tuple(params["ngram_range"])

    vectorizer = TfidfVectorizer(**params)
    vectorizer.vocabulary_ = {term: int(index) for term, index in document["vocabulary"].items()}
    # A vocabulary restored from disk was learned, not supplied by the caller,
    # so this stays False — it changes how the vectorizer validates input.
    vectorizer.fixed_vocabulary_ = False

    transformer = TfidfTransformer(sublinear_tf=params.get("sublinear_tf", False))
    transformer.idf_ = np.asarray(idf, dtype=np.float64)
    vectorizer._tfidf = transformer
    return vectorizer


def _rebuild_model(document: dict[str, Any], coef: np.ndarray, intercept: np.ndarray) -> Any:
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression()
    model.coef_ = np.asarray(coef, dtype=np.float64)
    model.intercept_ = np.asarray(intercept, dtype=np.float64)
    model.classes_ = np.asarray(document["classes"], dtype=object)
    model.n_features_in_ = int(document["n_features_in"])
    return model
