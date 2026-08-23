"""Train and compare the Phase 2 transformer document classifier."""

from __future__ import annotations

import argparse
import gc
from pathlib import Path

from .classifier import load_manifest, train_and_evaluate
from .deep_classifier import (
    DeepTrainingConfig,
    artifact_size_mb,
    render_comparison_report,
    train_and_evaluate_deep,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fine-tune a transformer and compare it with the classical baseline."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--deep-artifact-dir", type=Path, default=Path("artifacts/classifier_deep"))
    parser.add_argument(
        "--baseline-artifact-path",
        type=Path,
        default=Path("artifacts/classifier_baseline.pkl"),
    )
    parser.add_argument("--model-name", default="distilbert-base-uncased")
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--max-length", type=int, default=256)
    seed_group = parser.add_mutually_exclusive_group()
    seed_group.add_argument("--seed", type=int)
    seed_group.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        help="Run multiple seeds and report mean/sample standard deviation.",
    )
    parser.add_argument("--early-stopping-patience", type=int, default=2)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--confidence-threshold", type=float, default=0.65)
    args = parser.parse_args()

    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0 and 1.")

    seeds = args.seeds or [args.seed if args.seed is not None else 42]
    if len(set(seeds)) != len(seeds):
        parser.error("--seeds values must be unique.")

    splits = load_manifest(args.data_dir)
    baseline = train_and_evaluate(splits, confidence_threshold=args.confidence_threshold)
    baseline["classifier"].save(args.baseline_artifact_path)

    deep_runs: list[dict] = []
    selected_deep: dict | None = None
    selected_val_f1 = -1.0
    for seed in seeds:
        try:
            config = DeepTrainingConfig(
                model_name=args.model_name,
                epochs=args.epochs,
                batch_size=args.batch_size,
                learning_rate=args.learning_rate,
                weight_decay=args.weight_decay,
                max_length=args.max_length,
                seed=seed,
                early_stopping_patience=args.early_stopping_patience,
                device=args.device,
            )
        except ValueError as exc:
            parser.error(str(exc))

        print(f"[train_deep_classifier] starting seed {seed}")
        run = train_and_evaluate_deep(
            splits,
            config=config,
            confidence_threshold=args.confidence_threshold,
        )
        run_summary = {key: value for key, value in run.items() if key != "classifier"}
        deep_runs.append(run_summary)
        val_f1 = run["evaluations"]["val"]["macro_f1"]
        if val_f1 > selected_val_f1:
            selected_val_f1 = val_f1
            selected_deep = run_summary
            run["classifier"].save(args.deep_artifact_dir)
        del run
        gc.collect()

    if selected_deep is None:
        raise RuntimeError("No deep-classifier run completed.")

    report = render_comparison_report(
        baseline,
        selected_deep,
        data_dir=args.data_dir,
        baseline_artifact_mb=artifact_size_mb(args.baseline_artifact_path),
        deep_artifact_mb=artifact_size_mb(args.deep_artifact_dir),
        deep_runs=deep_runs,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "classifier_deep_vs_baseline.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")

    print(report)
    print(f"Report written to: {report_path}")
    print(f"Deep classifier artifact written to: {args.deep_artifact_dir}")


if __name__ == "__main__":
    main()
