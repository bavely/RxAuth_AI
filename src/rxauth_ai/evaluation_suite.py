"""One command that scores every layer against a threshold (README section 15).

Each layer already has its own benchmark and its own report. What was missing
is the question a maintainer actually asks before merging: *did anything get
worse?* Answering it meant running six commands and reading six markdown files
by eye, which is a check that quietly stops happening.

So this collects the headline metric of every layer into one scorecard, each
with a threshold, and exits non-zero when any of them is breached. A change
that moves a number now fails the build until somebody either fixes it or
edits the threshold on purpose — and editing a threshold is a visible, review-
able diff, which is the entire point.

**The thresholds are a ratchet, not an aspiration.** They are set at the
values the current code produces, so the gate catches regressions rather than
expressing hopes. Raising one is how progress is locked in. Lowering one is
allowed and is meant to be uncomfortable: it is a recorded decision that the
system got worse and somebody accepted it.

**What the numbers do not mean.** Every gold set here is synthetic and
authored in this repository. A score of 1.000 says the declared contract
holds. It is not evidence of clinical or production generalization, and
`docs/matching-gold.md` and `docs/extraction-gold.md` say so at more length.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .benchmark_criteria import benchmark_criteria
from .benchmark_extraction import benchmark_extraction
from .benchmark_matching import benchmark_matching
from .benchmark_retrieval import benchmark_retrieval
from .classifier import load_manifest, train_and_evaluate
from .generation import generate_checklist
from .groundedness import check_draft_groundedness
from .models import ClaimStatus
from .pipeline import run_pipeline
from .synthetic_case import build_case, build_policy

SUITE_VERSION = "eval-suite-v1"


@dataclass(frozen=True)
class Metric:
    """One measured number and the bound it has to stay inside."""

    layer: str
    name: str
    value: float
    floor: Optional[float] = None
    ceiling: Optional[float] = None

    @property
    def passed(self) -> bool:
        if self.floor is not None and self.value < self.floor:
            return False
        if self.ceiling is not None and self.value > self.ceiling:
            return False
        return True

    @property
    def bound(self) -> str:
        if self.floor is not None:
            return f">= {self.floor:.3f}"
        if self.ceiling is not None:
            return f"<= {self.ceiling:.3f}"
        return "unbounded"


def _classification_metrics(data_dir: Path) -> list[Metric]:
    results = train_and_evaluate(load_manifest(data_dir))
    return [
        # Trained fresh each run. The floor sits fractionally below the measured
        # value because this is the one layer whose exact score depends on a
        # solver's convergence rather than on a deterministic rule.
        Metric(
            "classification",
            "test macro F1",
            results["evaluations"]["test"]["macro_f1"],
            floor=0.95,
        ),
        Metric(
            "classification",
            "challenge macro F1",
            results["evaluations"]["challenge"]["macro_f1"],
            floor=0.88,
        ),
    ]


def _extraction_metrics(gold_path: Path) -> list[Metric]:
    results = benchmark_extraction(gold_path)
    metrics = []
    for split in ("test", "challenge"):
        evaluation = results["evaluations"][split]
        metrics += [
            Metric("extraction", f"{split} field F1", evaluation["field_f1"], floor=1.0),
            Metric(
                "extraction",
                f"{split} provenance span accuracy",
                evaluation["provenance_span_accuracy"],
                floor=1.0,
            ),
        ]
    return metrics


def _retrieval_metrics() -> list[Metric]:
    results = benchmark_retrieval()
    filtered = next(
        config
        for config in results["configurations"]
        if config["mode"] == "metadata+similarity" and config["embedding_model"] == "tfidf-v1"
    )
    vector_only = next(
        config
        for config in results["configurations"]
        if config["mode"] == "similarity_only" and config["embedding_model"] == "tfidf-v1"
    )
    return [
        Metric("retrieval", "correct-policy rate", filtered["correct_policy_rate"], floor=1.0),
        # The ablation is the claim README section 10 makes. If vector-only ever
        # catches up, the argument for filtering first needs re-making, not
        # quietly inheriting.
        Metric(
            "retrieval",
            "advantage over vector-only",
            filtered["correct_policy_rate"] - vector_only["correct_policy_rate"],
            floor=0.3,
        ),
        Metric(
            "retrieval",
            "declined when it should",
            filtered["abstention_correct"] / max(filtered["abstention_cases"], 1),
            floor=1.0,
        ),
    ]


def _criteria_metrics() -> list[Metric]:
    results = benchmark_criteria()
    return [
        Metric("criteria", "criterion F1", results["criterion_f1"], floor=1.0),
        Metric("criteria", "provenance accuracy", results["provenance_accuracy"], floor=1.0),
        Metric("criteria", "connective accuracy", results["connective_accuracy"], floor=1.0),
        # A dropped requirement makes a case read as readier than it is, so the
        # recall of what could *not* be structured is a safety metric.
        Metric("criteria", "unstructured recall", results["unstructured_recall"], floor=1.0),
    ]


def _matching_metrics(gold_path: Path) -> list[Metric]:
    results = benchmark_matching(gold_path)
    metrics = []
    for split in ("test", "challenge"):
        evaluation = results["evaluations"][split]
        metrics += [
            Metric(
                "matching", f"{split} result accuracy", evaluation["result_accuracy"], floor=1.0
            ),
            Metric("matching", f"{split} evidence F1", evaluation["evidence_f1"], floor=1.0),
            # The only ceiling in the suite. Every other error asks a human for
            # more work; this one tells them there is none.
            Metric(
                "matching",
                f"{split} false-support rate",
                evaluation["false_support_rate"],
                ceiling=0.0,
            ),
        ]
    return metrics


def _generation_metrics() -> list[Metric]:
    """Score the drafted checklist against the record it claims to describe."""
    case, policy = build_case(), build_policy()
    report = run_pipeline(case, policy)
    checklist = generate_checklist(report, case, policy)
    gate = check_draft_groundedness(checklist, report.evaluations, case)

    total = max(len(checklist.claims), 1)
    unsupported = gate.count(ClaimStatus.UNSUPPORTED) + gate.count(ClaimStatus.CONFLICTING)
    return [
        Metric("generation", "unsupported-claim rate", unsupported / total, ceiling=0.0),
        Metric(
            "generation",
            "claims carrying a citation",
            sum(1 for claim in checklist.claims if claim.policy_source is not None) / total,
            floor=1.0,
        ),
        Metric("generation", "gate passed", 1.0 if gate.passed else 0.0, floor=1.0),
    ]


def collect_metrics(
    *,
    data_dir: Path = Path("data"),
    extraction_gold: Path = Path("data/extraction_gold.jsonl"),
    matching_gold: Path = Path("data/matching_gold.jsonl"),
    include_classification: bool = True,
) -> list[Metric]:
    """Run every layer's benchmark and return the scorecard."""
    metrics: list[Metric] = []
    if include_classification:
        metrics += _classification_metrics(data_dir)
    metrics += _extraction_metrics(extraction_gold)
    metrics += _retrieval_metrics()
    metrics += _criteria_metrics()
    metrics += _matching_metrics(matching_gold)
    metrics += _generation_metrics()
    return metrics


def render_report(metrics: list[Metric]) -> str:
    failures = [metric for metric in metrics if not metric.passed]
    lines = [
        "# Evaluation suite",
        "",
        "_Reproducible: `rxauth-evaluate`._",
        "",
        "## Contract",
        "",
        f"- Suite: `{SUITE_VERSION}`",
        f"- Metrics: {len(metrics)} across {len({metric.layer for metric in metrics})} layers",
        "- Thresholds are a ratchet set at the values the current code produces. A regression "
        "fails the build; relaxing a threshold is a deliberate, reviewable diff.",
        "- Every gold set is synthetic and authored in this repository. These numbers validate "
        "the declared contracts, not clinical or production generalization.",
        "",
        f"**Result: {'PASS' if not failures else 'FAIL'}** "
        f"({len(metrics) - len(failures)}/{len(metrics)} within threshold)",
        "",
        "## Scorecard",
        "",
        "| Layer | Metric | Value | Threshold | Status |",
        "|---|---|---:|---:|:--|",
    ]
    for metric in metrics:
        status = "pass" if metric.passed else "**FAIL**"
        lines.append(
            f"| {metric.layer} | {metric.name} | {metric.value:.3f} | {metric.bound} | {status} |"
        )

    lines += ["", "## Breaches", ""]
    if not failures:
        lines.append("None.")
    else:
        for metric in failures:
            lines.append(
                f"- `{metric.layer}` / {metric.name}: {metric.value:.3f}, expected {metric.bound}."
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run every layer's benchmark and gate on its threshold."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument(
        "--skip-classification",
        action="store_true",
        help="Skip the layer that retrains a model (the slowest by far).",
    )
    args = parser.parse_args()

    metrics = collect_metrics(
        data_dir=args.data_dir, include_classification=not args.skip_classification
    )
    report = render_report(metrics)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "evaluation_suite.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"Report written to: {report_path}")

    failures = [metric for metric in metrics if not metric.passed]
    if failures:
        raise SystemExit(
            f"{len(failures)} metric(s) below threshold. Fix the regression, or change the "
            "threshold deliberately and say why in the commit."
        )


if __name__ == "__main__":
    main()
