"""RxAuth AI — document classifier baseline runner (main README §8, Phase 1).

Trains TF-IDF + Logistic Regression on the synthetic dataset in `data/` and
writes the evaluation report to `reports/classifier_baseline.md`.

Usage:
    rxauth-build-dataset
    rxauth-train-classifier
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .classifier import load_manifest, render_report_md, train_and_evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the baseline document classifier.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Dataset directory (default: ./data).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports"),
        help="Report directory (default: ./reports).",
    )
    args = parser.parse_args()

    splits = load_manifest(args.data_dir)
    results = train_and_evaluate(splits)

    report_md = render_report_md(results, args.data_dir)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "classifier_baseline.md"
    out_path.write_text(report_md, encoding="utf-8")

    print(report_md)
    print(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
