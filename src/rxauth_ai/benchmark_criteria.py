"""Gold-set benchmark for policy criteria extraction (README section 11, section 15).

A criterion is only correct when every part of it is: the type, the medication
it names, the operator, the threshold, the unit, and the required outcome. A
rule that reads "at least 12 weeks" as "at least 12 months" scores zero here,
because downstream it would turn a satisfied case into a failed one.

Three things are measured beyond field accuracy, because each is a way the
extractor could look right while being unsafe:

- **Provenance accuracy.** A criterion's cited span must actually index the
  policy page it claims. A structured requirement whose citation does not
  resolve is not auditable, whatever its fields say.
- **Unstructured routing.** A requirement no rule can handle must be *kept and
  flagged*, not dropped. Recall over the gold's unstructured items catches the
  failure mode where the extractor quietly shrinks the policy.
- **Connective detection.** Reading an ANY list as an ALL list inverts the
  reviewer's answer, so the detected connective is scored per policy.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import get_settings
from .criteria_extraction import (
    CRITERIA_EXTRACTOR_VERSION,
    DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD,
    CriteriaIssueKind,
    extract_criteria,
)
from .models import Criterion
from .policy_corpus import DEFAULT_POLICY_DIR, PolicyDocument, load_corpus

DEFAULT_GOLD_PATH = get_settings().data_dir / "policy_criteria_gold.jsonl"


class GoldCriterion(BaseModel):
    criterion_type: str
    medication: str | None = None
    operator: str | None = None
    expected_value: float | None = None
    unit: str | None = None
    required_outcome: str | None = None
    source_text: str


class GoldPolicy(BaseModel):
    policy_key: str
    connective: str
    expected_criteria: list[GoldCriterion] = Field(default_factory=list)
    expected_exclusions: int = 0
    expected_unstructured: int = 0


def load_gold(path: Path, documents: dict[str, PolicyDocument]) -> list[GoldPolicy]:
    """Load JSONL and reject a gold record that does not describe a real policy."""
    if not path.exists():
        raise FileNotFoundError(f"Gold criteria dataset not found: {path}")

    records: list[GoldPolicy] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            record = GoldPolicy.model_validate_json(raw)
        except Exception as exc:
            raise ValueError(f"Invalid gold JSON on line {line_number}: {exc}") from exc
        if record.policy_key in seen:
            raise ValueError(f"Duplicate gold policy_key: {record.policy_key}")
        seen.add(record.policy_key)

        document = documents.get(record.policy_key)
        if document is None:
            raise ValueError(f"Gold names unknown policy version: {record.policy_key}")
        for expected in record.expected_criteria:
            occurrences = sum(chunk.text.count(expected.source_text) for chunk in document.chunks)
            if occurrences != 1:
                raise ValueError(
                    f"{record.policy_key} gold source_text must occur exactly once; "
                    f"found {occurrences}: {expected.source_text!r}"
                )
        records.append(record)

    if not records:
        raise ValueError("Gold criteria dataset is empty.")
    missing = sorted(set(documents) - seen)
    if missing:
        raise ValueError(
            "Every policy version in the corpus needs a gold record, otherwise a policy the "
            f"extractor mishandles can go unmeasured. Missing: {', '.join(missing)}"
        )
    return records


def _signature(item: GoldCriterion | Criterion) -> tuple[Any, ...]:
    return (
        item.criterion_type,
        item.medication,
        item.operator,
        item.expected_value,
        item.unit,
        item.required_outcome,
        item.source_text if isinstance(item, GoldCriterion) else item.description,
    )


def _format_signature(signature: tuple[Any, ...]) -> str:
    kind, medication, operator, value, unit, outcome, text = signature
    comparison = " ".join(str(part) for part in (operator, value, unit) if part is not None)
    parts = [kind]
    if medication:
        parts.append(medication)
    if comparison:
        parts.append(comparison)
    if outcome:
        parts.append(f"outcome={outcome}")
    return f'{" / ".join(parts)} — "{text}"'


def _safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return _safe_divide(2 * precision * recall, precision + recall)


def _provenance_resolves(criterion: Criterion, document: PolicyDocument) -> bool:
    """Whether the criterion's cited span still points at the text it quotes."""
    source = criterion.provenance
    if source.page is None or source.start_char is None or source.source_text is None:
        return False
    chunk = next(
        (
            chunk
            for chunk in document.chunks
            if chunk.page == source.page
            and chunk.start_char == source.start_char
            and chunk.end_char == source.end_char
        ),
        None,
    )
    return chunk is not None and chunk.text == source.source_text


def benchmark_criteria(
    gold_path: Path = DEFAULT_GOLD_PATH,
    *,
    policy_dir: Path = DEFAULT_POLICY_DIR,
    confidence_threshold: float = DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    documents = {document.key: document for document in load_corpus(policy_dir)}
    records = load_gold(gold_path, documents)

    expected_counter: Counter[tuple[Any, ...]] = Counter()
    predicted_counter: Counter[tuple[Any, ...]] = Counter()
    per_policy: list[dict[str, Any]] = []
    provenance_total = 0
    provenance_correct = 0
    connective_correct = 0
    unstructured_expected = 0
    unstructured_found = 0
    exclusions_correct = 0
    elapsed = 0.0

    for record in records:
        document = documents[record.policy_key]
        started = time.perf_counter()
        extraction = extract_criteria(document, confidence_threshold=confidence_threshold)
        elapsed += time.perf_counter() - started

        expected = Counter(_signature(item) for item in record.expected_criteria)
        predicted = Counter(_signature(item) for item in extraction.criteria)
        expected_counter += expected
        predicted_counter += predicted

        for criterion in extraction.criteria:
            provenance_total += 1
            provenance_correct += _provenance_resolves(criterion, document)

        connective_ok = extraction.connective == record.connective
        connective_correct += connective_ok
        unstructured_expected += record.expected_unstructured
        flagged = sum(
            1
            for issue in extraction.issues
            if issue.kind is CriteriaIssueKind.UNSTRUCTURED_REQUIREMENT
        )
        unstructured_found += min(flagged, record.expected_unstructured)
        exclusions_ok = len(extraction.exclusions) == record.expected_exclusions
        exclusions_correct += exclusions_ok

        matched = sum((expected & predicted).values())
        per_policy.append(
            {
                "policy_key": record.policy_key,
                "expected": sum(expected.values()),
                "predicted": sum(predicted.values()),
                "matched": matched,
                "connective_expected": record.connective,
                "connective_predicted": extraction.connective,
                "connective_correct": connective_ok,
                "exclusions_expected": record.expected_exclusions,
                "exclusions_predicted": len(extraction.exclusions),
                "exclusions_correct": exclusions_ok,
                "unstructured_expected": record.expected_unstructured,
                "unstructured_flagged": flagged,
                "failures": [
                    {"kind": "false negative", "detail": _format_signature(signature)}
                    for signature in (expected - predicted)
                ]
                + [
                    {"kind": "false positive", "detail": _format_signature(signature)}
                    for signature in (predicted - expected)
                ],
            }
        )

    true_positive = sum((expected_counter & predicted_counter).values())
    false_positive = sum((predicted_counter - expected_counter).values())
    false_negative = sum((expected_counter - predicted_counter).values())
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)

    return {
        "extractor_version": CRITERIA_EXTRACTOR_VERSION,
        "confidence_threshold": confidence_threshold,
        "policies": len(records),
        "expected_criteria": sum(expected_counter.values()),
        "predicted_criteria": sum(predicted_counter.values()),
        "criterion_precision": precision,
        "criterion_recall": recall,
        "criterion_f1": _f1(precision, recall),
        "provenance_accuracy": _safe_divide(provenance_correct, provenance_total),
        "connective_accuracy": _safe_divide(connective_correct, len(records)),
        "exclusion_accuracy": _safe_divide(exclusions_correct, len(records)),
        "unstructured_recall": (
            _safe_divide(unstructured_found, unstructured_expected)
            if unstructured_expected
            else None
        ),
        "unstructured_expected": unstructured_expected,
        "latency_ms_per_policy": _safe_divide(elapsed * 1000, len(records)),
        "per_policy": per_policy,
    }


def render_report(results: dict[str, Any], gold_path: Path) -> str:
    unstructured = results["unstructured_recall"]
    lines = [
        "# Phase 4 policy criteria-extraction benchmark",
        "",
        "_Reproducible: `rxauth-benchmark-criteria`._",
        "",
        "## Contract",
        f"- Policy corpus: `data/policies/` ({results['policies']} policy versions)",
        f"- Gold criteria: `{gold_path.as_posix()}`",
        f"- Extractor: `{results['extractor_version']}`",
        f"- Review threshold: {results['confidence_threshold']:.2f}",
        "- A criterion is correct only when its type, medication, operator, threshold, unit, "
        "required outcome, **and** quoted source text all agree with the gold.",
        "- Every policy version in the corpus must have a gold record; the loader refuses a "
        "partial dataset.",
        "- Gold source text must occur exactly once in the policy it names.",
        "- The corpus is synthetic public-style policy text. These numbers describe the rules "
        "against this corpus, not real payer publications.",
        "",
        "## Results",
        "| Metric | Value |",
        "|---|---:|",
        f"| Gold criteria | {results['expected_criteria']} |",
        f"| Extracted criteria | {results['predicted_criteria']} |",
        f"| Criterion precision | {results['criterion_precision']:.3f} |",
        f"| Criterion recall | {results['criterion_recall']:.3f} |",
        f"| Criterion F1 | {results['criterion_f1']:.3f} |",
        f"| Provenance-span accuracy | {results['provenance_accuracy']:.3f} |",
        f"| Connective accuracy | {results['connective_accuracy']:.3f} |",
        f"| Exclusion-count accuracy | {results['exclusion_accuracy']:.3f} |",
        (
            f"| Unstructured-requirement recall | {unstructured:.3f} "
            f"({results['unstructured_expected']} expected) |"
            if unstructured is not None
            else "| Unstructured-requirement recall | n/a (none in gold) |"
        ),
        f"| Latency (ms/policy) | {results['latency_ms_per_policy']:.3f} |",
        "",
        "## Per policy",
        "| Policy version | Gold | Extracted | Matched | Connective | Exclusions | Unstructured |",
        "|---|---:|---:|---:|---|---|---:|",
    ]
    for policy in results["per_policy"]:
        connective = (
            policy["connective_predicted"]
            if policy["connective_correct"]
            else f"{policy['connective_predicted']} (expected {policy['connective_expected']})"
        )
        exclusions = (
            str(policy["exclusions_predicted"])
            if policy["exclusions_correct"]
            else f"{policy['exclusions_predicted']} (expected {policy['exclusions_expected']})"
        )
        lines.append(
            f"| `{policy['policy_key']}` | {policy['expected']} | {policy['predicted']} | "
            f"{policy['matched']} | {connective} | {exclusions} | "
            f"{policy['unstructured_flagged']} |"
        )

    lines += ["", "## Failures"]
    failures = [
        (policy["policy_key"], failure)
        for policy in results["per_policy"]
        for failure in policy["failures"]
    ]
    if not failures:
        lines.append("None.")
    else:
        lines += ["| Policy version | Kind | Detail |", "|---|---|---|"]
        for policy_key, failure in failures[:30]:
            detail = failure["detail"].replace("|", "\\|")
            lines.append(f"| `{policy_key}` | {failure['kind']} | {detail} |")

    lines += [
        "",
        "## Interpretation",
        "",
        "The score says the rules read this corpus correctly. It does not say they read payer "
        "prose correctly: the sentences were authored locally in the forms the rules expect, so "
        "this measures the declared contract — normalization, provenance, connective detection, "
        "and the routing of what could not be structured — not generalization to real policy "
        "language.",
        "",
        "The unstructured row is the one to watch as the corpus grows. A rule set that silently "
        "drops requirements it cannot parse will keep a perfect criterion F1 while shrinking the "
        "policy a case is judged against, which is the more dangerous error of the two: the case "
        "reads as readier than it is. Recall over the gold's unstructured items is what makes "
        "that failure visible.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark policy criteria extraction against gold structured criteria."
    )
    parser.add_argument("--gold-path", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    parser.add_argument("--output-dir", type=Path, default=get_settings().reports_dir)
    parser.add_argument(
        "--confidence-threshold", type=float, default=DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD
    )
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0 and 1.")

    results = benchmark_criteria(
        args.gold_path,
        policy_dir=args.policy_dir,
        confidence_threshold=args.confidence_threshold,
    )
    if args.json_only:
        print(json.dumps(results, indent=2))
        return

    report = render_report(results, args.gold_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "criteria_extraction.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
