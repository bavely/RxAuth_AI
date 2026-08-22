#!/usr/bin/env python3
"""RxAuth AI — document classifier baseline runner (main README §8, Phase 1).

Trains TF-IDF + Logistic Regression on the synthetic dataset in `data/` and
writes the evaluation report to `reports/classifier_baseline.md`.

Usage:
    python data/build_dataset.py       # generate the dataset first (one-time / reproducible)
    python train_classifier_baseline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from files.classifier import load_manifest, render_report_md, train_and_evaluate


def main() -> None:
    data_dir = Path(__file__).parent / "data"
    splits = load_manifest(data_dir)
    results = train_and_evaluate(splits)

    report_md = render_report_md(results, data_dir)

    out_dir = Path(__file__).parent / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "classifier_baseline.md"
    out_path.write_text(report_md, encoding="utf-8")

    print(report_md)
    print(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
