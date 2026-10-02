"""RxAuth AI — document classifier baseline runner (main README §8, Phase 1).

Trains TF-IDF + Logistic Regression on the synthetic dataset in `data/` and
writes the evaluation report to `reports/classifier_baseline.md`.
The fitted bundle is saved as a pickle-free artifact directory in
`artifacts/classifier_baseline/` (model.json + weights.npz + manifest.json).

Usage:
    rxauth-build-dataset
    rxauth-train-classifier
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .classifier import load_manifest, render_report_md, train_and_evaluate
from .config import get_settings
from .registry import fingerprint_training_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the baseline document classifier.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=get_settings().data_dir,
        help="Dataset directory (default: ./data).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=get_settings().reports_dir,
        help="Report directory (default: ./reports).",
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(get_settings().classifier_path),
        help="Artifact directory (default: ./artifacts/classifier_baseline).",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.65,
        help="Predictions below this confidence require human review (default: 0.65).",
    )
    args = parser.parse_args()

    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0 and 1.")

    splits = load_manifest(args.data_dir)
    results = train_and_evaluate(splits, confidence_threshold=args.confidence_threshold)

    report_md = render_report_md(results, args.data_dir)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "classifier_baseline.md"
    out_path.write_text(report_md, encoding="utf-8", newline="\n")
    train = splits["train"]
    results["classifier"].save(
        args.artifact_path,
        metrics={
            "test_macro_f1": results["evaluations"]["test"]["macro_f1"],
            "challenge_macro_f1": results["evaluations"]["challenge"]["macro_f1"],
            "test_accuracy": results["test_accuracy"],
        },
        split_sizes={name: len(splits[name]) for name in sorted(splits)},
        training_data_fingerprint=fingerprint_training_data(
            list(train.case_ids), list(train.labels)
        ),
    )

    print(report_md)
    print(f"Report written to: {out_path}")
    print(f"Classifier artifact written to: {args.artifact_path}")


if __name__ == "__main__":
    main()
