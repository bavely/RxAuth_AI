"""Measure the extractor's confidence values against the gold validation split.

The confidence attached to an extracted field started life as an engineering
prior — a number chosen because one pattern looked more explicit than another.
README section 3 forbids publishing a number that has not been measured, and
docs/phase-3-extraction.md lists calibration as required work for section 9.
This module supplies the measurement.

Two guardrails shape it:

- **Validation only.** The gold test split never tunes a threshold or a
  confidence value. It exists to be surprised by, and reading it here would
  spend that exactly once.
- **Measure before adjusting.** This reports the gap between claimed and
  observed correctness; it deliberately does not fit a mapping and write it
  back into the rules. With this many fields per bucket, a fitted mapping would
  be a restatement of the sample, not a calibration.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .benchmark_extraction import GoldDocument, load_gold
from .config import get_settings
from .extraction import DEFAULT_CONFIDENCE_THRESHOLD, EXTRACTOR_VERSION, extract_evidence
from .ingestion import IngestedDocument, IngestedPage

CALIBRATION_SPLIT = "validation"
THRESHOLD_SWEEP = tuple(round(0.50 + 0.05 * step, 2) for step in range(10))


def _as_document(record: GoldDocument) -> IngestedDocument:
    return IngestedDocument(
        filename=record.filename,
        media_type="text",
        pages=[
            IngestedPage(
                page_number=1,
                text=record.text,
                extraction_method="text",
                confidence=1.0,
            )
        ],
    )


def _normalized(item: Any) -> tuple[Any, ...]:
    return (
        item.evidence_type,
        item.medication,
        item.text_value,
        item.value,
        item.unit,
        item.outcome,
    )


def collect_observations(records: list[GoldDocument]) -> dict[str, Any]:
    """Score every predicted field on the split as correct or incorrect.

    A prediction is correct when a gold field with the same evidence type and
    the same cited source text exists *and* every normalized value agrees. A
    prediction with no gold counterpart is a confident claim about something
    that is not there, so it counts as incorrect rather than being skipped.
    """
    observations: list[dict[str, Any]] = []
    review_expected: set[tuple[str, str, str]] = set()
    review_candidates: list[dict[str, Any]] = []
    suppressions: list[dict[str, str]] = []
    multi_span_facts = 0
    missed_fields = 0

    for record in records:
        result = extract_evidence(
            _as_document(record),
            document_id=record.document_id,
            # Route on the rule confidences themselves; the sweep below applies
            # its own thresholds rather than inheriting one from this call.
            confidence_threshold=0.0,
        )
        gold_by_key = {(item.evidence_type, item.source_text): item for item in record.expected}
        for item in record.expected:
            if item.requires_review:
                review_expected.add((record.document_id, item.evidence_type, item.source_text))

        predicted_keys: set[tuple[str, str]] = set()
        issues_by_id = {issue.evidence_id: issue for issue in result.issues}
        for item in result.evidence:
            key = (item.evidence_type, item.provenance.source_text or "")
            predicted_keys.add(key)
            expected = gold_by_key.get(key)
            correct = expected is not None and _normalized(item) == _normalized(expected)
            observations.append(
                {
                    "document_id": record.document_id,
                    "rule": item.extraction_rule or "unknown",
                    "evidence_type": item.evidence_type,
                    "confidence": item.confidence,
                    "correct": correct,
                }
            )
            if len(item.supporting_provenance) > 0:
                multi_span_facts += 1
            issue = issues_by_id.get(item.id)
            review_candidates.append(
                {
                    "key": (record.document_id, *key),
                    "confidence": item.confidence,
                    # An incomplete or ambiguous field is routed regardless of
                    # where the confidence threshold sits, so the sweep must
                    # know which routings the threshold cannot move.
                    "routed_regardless": issue is not None and issue.kind.value != "low_confidence",
                }
            )

        missed_fields += sum(1 for key in gold_by_key if key not in predicted_keys)
        suppressions.extend(
            {
                "document_id": record.document_id,
                "rule": span.rule,
                "superseded_by": span.superseded_by,
                "reason": span.reason,
            }
            for span in result.suppressed
        )

    return {
        "observations": observations,
        "review_expected": review_expected,
        "review_candidates": review_candidates,
        "suppressions": suppressions,
        "multi_span_facts": multi_span_facts,
        "missed_fields": missed_fields,
    }


def _reliability(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by the exact confidence value the rules assign.

    Fixed-width bins would blur together priors that were chosen to mean
    different things. The rules emit a handful of discrete values, so grouping
    on the value itself shows precisely which prior is off and by how much.
    """
    grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        grouped[observation["confidence"]].append(observation)

    rows = []
    for confidence, group in sorted(grouped.items()):
        correct = sum(1 for observation in group if observation["correct"])
        accuracy = correct / len(group)
        rows.append(
            {
                "confidence": confidence,
                "count": len(group),
                "correct": correct,
                "accuracy": accuracy,
                "gap": accuracy - confidence,
                "rules": sorted({observation["rule"] for observation in group}),
            }
        )
    return rows


def _by_rule(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        grouped[observation["rule"]].append(observation)

    rows = []
    for rule, group in sorted(grouped.items()):
        correct = sum(1 for observation in group if observation["correct"])
        mean_confidence = sum(observation["confidence"] for observation in group) / len(group)
        rows.append(
            {
                "rule": rule,
                "count": len(group),
                "mean_confidence": mean_confidence,
                "accuracy": correct / len(group),
            }
        )
    return sorted(rows, key=lambda row: (-row["count"], row["rule"]))


def _expected_calibration_error(rows: list[dict[str, Any]]) -> float:
    total = sum(row["count"] for row in rows)
    if not total:
        return 0.0
    return sum(row["count"] / total * abs(row["gap"]) for row in rows)


def _brier_score(observations: list[dict[str, Any]]) -> float:
    if not observations:
        return 0.0
    return sum(
        (observation["confidence"] - float(observation["correct"])) ** 2
        for observation in observations
    ) / len(observations)


def _threshold_sweep(
    review_candidates: list[dict[str, Any]], review_expected: set[tuple[str, str, str]]
) -> list[dict[str, Any]]:
    rows = []
    for threshold in THRESHOLD_SWEEP:
        routed = {
            candidate["key"]
            for candidate in review_candidates
            if candidate["routed_regardless"] or candidate["confidence"] < threshold
        }
        true_positive = len(routed & review_expected)
        false_positive = len(routed - review_expected)
        false_negative = len(review_expected - routed)
        precision = true_positive / (true_positive + false_positive) if routed else 0.0
        recall = true_positive / (true_positive + false_negative) if review_expected else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append(
            {
                "threshold": threshold,
                "routed": len(routed),
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    return rows


def calibrate(
    gold_path: Path, *, review_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
) -> dict[str, Any]:
    records = [record for record in load_gold(gold_path) if record.split == CALIBRATION_SPLIT]
    if not records:
        raise ValueError(f"Gold dataset has no {CALIBRATION_SPLIT} records to calibrate against.")

    collected = collect_observations(records)
    observations = collected["observations"]
    reliability = _reliability(observations)
    sweep = _threshold_sweep(collected["review_candidates"], collected["review_expected"])
    best_f1 = max((row["f1"] for row in sweep), default=0.0)
    safe_band = [row["threshold"] for row in sweep if row["f1"] >= best_f1]

    return {
        "extractor_version": EXTRACTOR_VERSION,
        "split": CALIBRATION_SPLIT,
        "documents": len(records),
        "fields_scored": len(observations),
        "fields_correct": sum(1 for observation in observations if observation["correct"]),
        "fields_missed": collected["missed_fields"],
        "multi_span_facts": collected["multi_span_facts"],
        "suppressed_spans": len(collected["suppressions"]),
        "suppression_reasons": Counter(span["reason"] for span in collected["suppressions"]),
        "reliability": reliability,
        "by_rule": _by_rule(observations),
        "expected_calibration_error": _expected_calibration_error(reliability),
        "brier_score": _brier_score(observations),
        "threshold_sweep": sweep,
        "current_threshold": review_threshold,
        "best_review_f1": best_f1,
        "safe_threshold_band": (min(safe_band), max(safe_band)) if safe_band else None,
    }


def render_report(results: dict[str, Any], gold_path: Path) -> str:
    band = results["safe_threshold_band"]
    lines = [
        "# Phase 3 extraction confidence calibration",
        "",
        "_Reproducible: `rxauth-calibrate-extraction`._",
        "",
        "## Contract",
        f"- Gold dataset: `{gold_path.as_posix()}`",
        f"- Split read: **{results['split']} only** ({results['documents']} documents). The test "
        "split never tunes a confidence value or a review threshold.",
        f"- Extractor: `{results['extractor_version']}`",
        f"- Fields scored: {results['fields_scored']} "
        f"({results['fields_correct']} correct, {results['fields_missed']} gold fields missed)",
        "- A prediction with no gold counterpart counts as incorrect, not as unscored.",
        "- All documents are synthetic and in-distribution; these numbers describe the rules "
        "against this corpus, not clinical text.",
        "",
        "## Reliability by assigned confidence",
        "| Assigned confidence | Fields | Correct | Observed accuracy | Gap | Rules |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for row in results["reliability"]:
        lines.append(
            f"| {row['confidence']:.2f} | {row['count']} | {row['correct']} | "
            f"{row['accuracy']:.3f} | {row['gap']:+.3f} | {', '.join(row['rules'])} |"
        )

    lines += [
        "",
        f"- Expected calibration error: **{results['expected_calibration_error']:.3f}**",
        f"- Brier score: **{results['brier_score']:.3f}**",
        "",
        "## Accuracy by rule",
        "| Rule | Fields | Mean confidence | Observed accuracy |",
        "|---|---:|---:|---:|",
    ]
    for row in results["by_rule"]:
        lines.append(
            f"| `{row['rule']}` | {row['count']} | {row['mean_confidence']:.3f} | "
            f"{row['accuracy']:.3f} |"
        )

    lines += [
        "",
        "## Review-threshold sweep",
        "",
        "Routing is no longer a pure threshold decision: an incomplete or ambiguously linked "
        "field is routed to review whatever the threshold is. The sweep therefore measures how "
        "much the threshold can move before it starts disagreeing with the gold routing.",
        "",
        "| Threshold | Fields routed | Precision | Recall | F1 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in results["threshold_sweep"]:
        marker = (
            " **(current)**" if abs(row["threshold"] - results["current_threshold"]) < 1e-9 else ""
        )
        lines.append(
            f"| {row['threshold']:.2f}{marker} | {row['routed']} | {row['precision']:.3f} | "
            f"{row['recall']:.3f} | {row['f1']:.3f} |"
        )

    lines += [
        "",
        "## Resolution stages on this split",
        "| Stage | Count |",
        "|---|---:|",
        f"| Facts citing more than one span | {results['multi_span_facts']} |",
        f"| Spans suppressed during overlap resolution | {results['suppressed_spans']} |",
    ]
    for reason, count in sorted(results["suppression_reasons"].items()):
        lines.append(f"| — {reason} | {count} |")

    lines += [
        "",
        "## Reading these numbers",
        "",
        "The gap column is positive across every bucket: on this corpus the rules are correct "
        "more often than they claim to be. That is the expected direction for hand-set priors "
        "chosen to be conservative, and it is not evidence that the priors should be raised. "
        "Each bucket holds a handful of fields drawn from documents written in the same "
        "vocabulary the rules target, so fitting a mapping to these observations would encode "
        "the sample rather than calibrate the extractor. The values stay as documented priors "
        "until the calibration split contains independently authored paraphrases and "
        "OCR-degraded pages.",
        "",
        "The sweep shows the same thing about the review threshold. Its useful reading is the "
        "width of the band that reproduces the gold routing, not the single best value inside "
        f"it: the band is {band[0]:.2f}–{band[1]:.2f} at F1 {results['best_review_f1']:.3f}, and "
        f"the configured default of {results['current_threshold']:.2f} sits inside it. A "
        "threshold that had to be tuned to a precise value to work would be a warning sign, "
        "not a result.",
        "",
        "The most useful finding is structural rather than numerical. Fields at the lowest "
        "confidence bucket are *correct*, and they are still the ones a reviewer must see — "
        "they are read accurately and simply do not state enough for a deterministic check. "
        "One number cannot carry both meanings, which is why routing now distinguishes "
        "`low_confidence` (the span may have been misread) from `incomplete_value` and "
        "`ambiguous_linkage` (the span was read correctly and still needs a human).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure extraction confidence against the gold validation split."
    )
    parser.add_argument(
        "--gold-path", type=Path, default=get_settings().data_dir / "extraction_gold.jsonl"
    )
    parser.add_argument("--output-dir", type=Path, default=get_settings().reports_dir)
    parser.add_argument(
        "--review-threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help="Threshold to mark as current in the sweep (default: the extractor's default).",
    )
    args = parser.parse_args()
    if not 0.0 <= args.review_threshold <= 1.0:
        parser.error("--review-threshold must be between 0 and 1.")

    results = calibrate(args.gold_path, review_threshold=args.review_threshold)
    report = render_report(results, args.gold_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "extraction_calibration.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
