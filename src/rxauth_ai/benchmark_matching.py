"""Gold evaluation for criteria-to-evidence matching (README §12)."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .matching import MATCHER_VERSION, NORMALIZATION_VERSION, evaluate_criterion
from .models import Case, Criterion, CriterionResult, Evidence, Provenance


class GoldEvidence(BaseModel):
    id: str
    evidence_type: str
    medication: Optional[str] = None
    text_value: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    outcome: Optional[str] = None
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    source_text: str


class GoldCriterion(BaseModel):
    criterion_type: str
    medication: Optional[str] = None
    operator: Optional[Literal[">=", "<=", ">", "<", "==", "exists"]] = None
    expected_value: Optional[float] = None
    unit: Optional[str] = None
    required_outcome: Optional[str] = None
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    source_text: str


class GoldMatch(BaseModel):
    match_id: str
    split: Literal["validation", "test", "challenge"]
    medication: str = "Drug A"
    indication: str = "Example Condition"
    criterion: GoldCriterion
    evidence: list[GoldEvidence] = Field(default_factory=list)
    expected_result: CriterionResult
    expected_evidence_ids: list[str] = Field(default_factory=list)
    note: Optional[str] = None


def load_gold(path: Path) -> list[GoldMatch]:
    if not path.is_file():
        raise FileNotFoundError(f"Matching gold dataset not found: {path}")
    records: list[GoldMatch] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            record = GoldMatch.model_validate_json(raw)
        except Exception as exc:
            raise ValueError(f"Invalid matching gold on line {line_number}: {exc}") from exc
        if record.match_id in seen:
            raise ValueError(f"Duplicate match_id: {record.match_id}")
        seen.add(record.match_id)
        evidence_ids = [item.id for item in record.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError(f"{record.match_id} has duplicate evidence IDs.")
        unknown = set(record.expected_evidence_ids) - set(evidence_ids)
        if unknown:
            raise ValueError(
                f"{record.match_id} expects evidence IDs absent from the case: {sorted(unknown)}"
            )
        records.append(record)
    if not records:
        raise ValueError("Matching gold dataset is empty.")
    if {record.split for record in records} != {"validation", "test", "challenge"}:
        raise ValueError("Matching gold must contain validation, test, and challenge records.")
    return records


def _models(record: GoldMatch) -> tuple[Case, Criterion]:
    evidence = [
        Evidence(
            id=item.id,
            evidence_type=item.evidence_type,
            medication=item.medication,
            text_value=item.text_value,
            value=item.value,
            unit=item.unit,
            outcome=item.outcome,
            confidence=item.confidence,
            provenance=Provenance(
                document_id=item.id.split("-")[0],
                filename=f"{item.id}.txt",
                page=1,
                start_char=0,
                end_char=len(item.source_text),
                source_text=item.source_text,
            ),
            extraction_method="matching-gold",
        )
        for item in record.evidence
    ]
    case = Case(
        id=record.match_id,
        patient_synthetic_id=f"SYNTH-{record.match_id}",
        payer="Example Health Plan",
        medication=record.medication,
        indication=record.indication,
        pa_required=True,
        evidence=evidence,
    )
    item = record.criterion
    criterion = Criterion(
        id=f"{record.match_id}-C1",
        policy_id="PA-GOLD",
        description=item.source_text,
        criterion_type=item.criterion_type,
        medication=item.medication,
        operator=item.operator,
        expected_value=item.expected_value,
        unit=item.unit,
        required_outcome=item.required_outcome,
        confidence=item.confidence,
        provenance=Provenance(
            document_id="PA-GOLD",
            filename="policy.txt",
            page=3,
            start_char=0,
            end_char=len(item.source_text),
            source_text=item.source_text,
        ),
    )
    return case, criterion


def _safe_divide(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return _safe_divide(2 * precision * recall, precision + recall)


def _evaluate(records: list[GoldMatch]) -> dict[str, Any]:
    result_pairs: list[tuple[CriterionResult, CriterionResult]] = []
    expected_evidence: set[tuple[str, str]] = set()
    predicted_evidence: set[tuple[str, str]] = set()
    expected_candidates: set[tuple[str, str]] = set()
    predicted_candidates: set[tuple[str, str]] = set()
    false_support = 0
    predicted_support = 0
    citation_correct = 0
    citation_total = 0
    failures: list[dict[str, str]] = []
    started = time.perf_counter()

    for record in records:
        case, criterion = _models(record)
        evaluation = evaluate_criterion(criterion, case)
        result_pairs.append((record.expected_result, evaluation.result))
        expected_evidence.update((record.match_id, item) for item in record.expected_evidence_ids)
        predicted_evidence.update(
            (record.match_id, item) for item in evaluation.supporting_evidence_ids
        )
        expected_candidates.update((record.match_id, item) for item in record.expected_evidence_ids)
        predicted_candidates.update(
            (record.match_id, item) for item in evaluation.candidate_evidence_ids
        )
        if evaluation.result is CriterionResult.SATISFIED:
            predicted_support += 1
            if record.expected_result is not CriterionResult.SATISFIED:
                false_support += 1
        evidence_by_id = {item.id: item for item in case.evidence}
        for evidence_id in evaluation.supporting_evidence_ids:
            citation_total += 1
            evidence = evidence_by_id.get(evidence_id)
            if evidence is not None and evidence.provenance.source_text:
                citation_correct += 1
        if (
            evaluation.result is not record.expected_result
            or set(evaluation.supporting_evidence_ids) != set(record.expected_evidence_ids)
        ):
            failures.append(
                {
                    "match_id": record.match_id,
                    "expected": (
                        f"{record.expected_result.value} {sorted(record.expected_evidence_ids)}"
                    ),
                    "predicted": (
                        f"{evaluation.result.value} {sorted(evaluation.supporting_evidence_ids)}"
                    ),
                }
            )

    latency = time.perf_counter() - started
    result_accuracy = _safe_divide(
        sum(expected is predicted for expected, predicted in result_pairs), len(result_pairs)
    )
    per_class_f1: dict[str, float] = {}
    for result in CriterionResult:
        true_positive = sum(
            expected is result and predicted is result for expected, predicted in result_pairs
        )
        false_positive = sum(
            expected is not result and predicted is result for expected, predicted in result_pairs
        )
        false_negative = sum(
            expected is result and predicted is not result for expected, predicted in result_pairs
        )
        precision = _safe_divide(true_positive, true_positive + false_positive)
        recall = _safe_divide(true_positive, true_positive + false_negative)
        per_class_f1[result.value] = _f1(precision, recall)

    evidence_tp = len(expected_evidence & predicted_evidence)
    evidence_precision = _safe_divide(evidence_tp, len(predicted_evidence))
    evidence_recall = _safe_divide(evidence_tp, len(expected_evidence))
    candidate_tp = len(expected_candidates & predicted_candidates)

    def recall_for(result: CriterionResult) -> float:
        relevant = [pair for pair in result_pairs if pair[0] is result]
        return _safe_divide(sum(predicted is result for _, predicted in relevant), len(relevant))

    return {
        "records": len(records),
        "result_accuracy": result_accuracy,
        "result_macro_f1": sum(per_class_f1.values()) / len(per_class_f1),
        "per_class_f1": per_class_f1,
        "evidence_precision": evidence_precision,
        "evidence_recall": evidence_recall,
        "evidence_f1": _f1(evidence_precision, evidence_recall),
        "retrieval_recall": _safe_divide(candidate_tp, len(expected_candidates)),
        "false_support_rate": _safe_divide(false_support, predicted_support),
        "missing_recall": recall_for(CriterionResult.MISSING),
        "ambiguity_recall": recall_for(CriterionResult.AMBIGUOUS),
        "review_recall": recall_for(CriterionResult.HUMAN_REVIEW_REQUIRED),
        "citation_correctness": _safe_divide(citation_correct, citation_total),
        "latency_ms_per_match": _safe_divide(latency * 1000, len(records)),
        "failures": failures,
    }


def benchmark_matching(gold_path: Path) -> dict[str, Any]:
    records = load_gold(gold_path)
    return {
        "matcher_version": MATCHER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "records_total": len(records),
        "evaluations": {
            split: _evaluate([record for record in records if record.split == split])
            for split in ("validation", "test", "challenge")
        },
    }


def render_report(results: dict[str, Any], gold_path: Path) -> str:
    lines = [
        "# Phase 5 criteria-to-evidence matching benchmark",
        "",
        "_Reproducible: `rxauth-benchmark-matching`._",
        "",
        "## Contract",
        "",
        f"- Gold dataset: `{gold_path.as_posix()}` ({results['records_total']} matches)",
        f"- Matcher: `{results['matcher_version']}`",
        f"- Normalization: `{results['normalization_version']}`",
        "- Exact evidence IDs are scored alongside the five-state result; the right status with "
        "the wrong source is a failure.",
        "- The default ambiguity interpreter abstains. No model-generated value is present in "
        "these results.",
        "- All cases are locally authored and synthetic; metrics validate this contract, not "
        "clinical generalization.",
        "",
        "## Results",
        "",
        "| Split | Matches | Result accuracy | Macro F1 | Evidence F1 | Retrieval recall | "
        "False support | Missing recall | Ambiguity recall | Review recall | Citation accuracy | "
        "Latency (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for split, metrics in results["evaluations"].items():
        lines.append(
            f"| {split} | {metrics['records']} | {metrics['result_accuracy']:.3f} | "
            f"{metrics['result_macro_f1']:.3f} | {metrics['evidence_f1']:.3f} | "
            f"{metrics['retrieval_recall']:.3f} | {metrics['false_support_rate']:.3f} | "
            f"{metrics['missing_recall']:.3f} | {metrics['ambiguity_recall']:.3f} | "
            f"{metrics['review_recall']:.3f} | {metrics['citation_correctness']:.3f} | "
            f"{metrics['latency_ms_per_match']:.3f} |"
        )

    for split, metrics in results["evaluations"].items():
        lines += ["", f"## {split.capitalize()} failures"]
        if not metrics["failures"]:
            lines.append("None.")
        else:
            lines += ["| Match | Expected | Predicted |", "|---|---|---|"]
            for failure in metrics["failures"]:
                lines.append(
                    f"| {failure['match_id']} | {failure['expected']} | {failure['predicted']} |"
                )

    lines += [
        "",
        "## Interpretation",
        "",
        "The benchmark is designed around unsafe shortcuts: selecting only the highest-confidence "
        "fact, accepting the wrong diagnosis, approximating calendar months, silently choosing "
        "one side of contradictory evidence, or treating an abstention as missing. False-support "
        "rate is reported explicitly because an unsupported SATISFIED result is the most dangerous "
        "matching error.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark criteria-to-evidence matching.")
    parser.add_argument("--gold-path", type=Path, default=Path("data/matching_gold.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    results = benchmark_matching(args.gold_path)
    report = render_report(results, args.gold_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "matching_evaluation.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
