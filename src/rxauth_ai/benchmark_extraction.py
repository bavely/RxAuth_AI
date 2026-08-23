"""Gold-set benchmark for provenance-preserving information extraction."""

from __future__ import annotations

import argparse
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .extraction import DEFAULT_CONFIDENCE_THRESHOLD, EXTRACTOR_VERSION, extract_evidence
from .ingestion import IngestedDocument, IngestedPage
from .models import Evidence


class GoldEvidence(BaseModel):
    evidence_type: str
    medication: str | None = None
    text_value: str | None = None
    value: float | None = None
    unit: str | None = None
    outcome: str | None = None
    source_text: str
    requires_review: bool = False


class GoldDocument(BaseModel):
    document_id: str
    split: Literal["validation", "test"]
    filename: str
    text: str
    expected: list[GoldEvidence] = Field(default_factory=list)


def load_gold(path: Path) -> list[GoldDocument]:
    """Load JSONL and reject ambiguous source spans or duplicate document IDs."""
    if not path.exists():
        raise FileNotFoundError(f"Gold extraction dataset not found: {path}")
    records: list[GoldDocument] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = GoldDocument.model_validate_json(raw_line)
        except Exception as exc:
            raise ValueError(f"Invalid gold JSON on line {line_number}: {exc}") from exc
        if record.document_id in seen_ids:
            raise ValueError(f"Duplicate gold document_id: {record.document_id}")
        seen_ids.add(record.document_id)
        for expected in record.expected:
            occurrences = record.text.count(expected.source_text)
            if occurrences != 1:
                raise ValueError(
                    f"{record.document_id} source_text must occur exactly once; "
                    f"found {occurrences}: {expected.source_text!r}"
                )
        records.append(record)
    if not records:
        raise ValueError("Gold extraction dataset is empty.")
    present_splits = {record.split for record in records}
    if present_splits != {"validation", "test"}:
        raise ValueError("Gold extraction dataset must contain validation and test records.")
    return records


def _normalized_signature(item: GoldEvidence | Evidence) -> tuple[Any, ...]:
    return (
        item.evidence_type,
        item.medication,
        item.text_value,
        item.value,
        item.unit,
        item.outcome,
    )


def _exact_signature(item: GoldEvidence | Evidence) -> tuple[Any, ...]:
    source_text = (
        item.source_text if isinstance(item, GoldEvidence) else item.provenance.source_text
    )
    return (*_normalized_signature(item), source_text)


def _safe_divide(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return _safe_divide(2 * precision * recall, precision + recall)


def _format_signature(signature: tuple[Any, ...]) -> str:
    names = ("document", "type", "medication", "text", "value", "unit", "outcome", "source")
    return ", ".join(
        f"{name}={value!r}"
        for name, value in zip(names, signature, strict=True)
        if value is not None
    )


def _evaluate_records(
    records: list[GoldDocument], *, confidence_threshold: float
) -> dict[str, Any]:
    expected_counter: Counter[tuple[Any, ...]] = Counter()
    predicted_counter: Counter[tuple[Any, ...]] = Counter()
    normalized_correct = 0
    aligned_fields = 0
    span_correct = 0
    expected_review_keys: set[tuple[str, str, str]] = set()
    predicted_review_keys: set[tuple[str, str, str]] = set()
    expected_review_documents: set[str] = set()
    predicted_review_documents: set[str] = set()
    failures: list[dict[str, str]] = []
    extraction_seconds = 0.0

    for record in records:
        ingested = IngestedDocument(
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
        start = time.perf_counter()
        result = extract_evidence(
            ingested,
            document_id=record.document_id,
            confidence_threshold=confidence_threshold,
        )
        extraction_seconds += time.perf_counter() - start
        issue_ids = {issue.evidence_id for issue in result.issues}

        expected_by_alignment: dict[tuple[str, str], GoldEvidence] = {}
        for item in record.expected:
            signature = (record.document_id, *_exact_signature(item))
            expected_counter[signature] += 1
            alignment_key = (item.evidence_type, item.source_text)
            expected_by_alignment[alignment_key] = item
            if item.requires_review:
                expected_review_keys.add((record.document_id, *alignment_key))
                expected_review_documents.add(record.document_id)

        for item in result.evidence:
            signature = (record.document_id, *_exact_signature(item))
            predicted_counter[signature] += 1
            source_text = item.provenance.source_text or ""
            alignment_key = (item.evidence_type, source_text)
            if item.id in issue_ids:
                predicted_review_keys.add((record.document_id, *alignment_key))
                predicted_review_documents.add(record.document_id)
            expected = expected_by_alignment.get(alignment_key)
            if expected is None:
                continue
            aligned_fields += 1
            if _normalized_signature(item) == _normalized_signature(expected):
                normalized_correct += 1
            expected_start = record.text.index(expected.source_text)
            if (
                item.provenance.page == 1
                and item.provenance.start_char == expected_start
                and item.provenance.end_char == expected_start + len(expected.source_text)
                and item.provenance.source_text == expected.source_text
            ):
                span_correct += 1

    true_positive = sum((expected_counter & predicted_counter).values())
    false_positive_counter = predicted_counter - expected_counter
    false_negative_counter = expected_counter - predicted_counter
    false_positive = sum(false_positive_counter.values())
    false_negative = sum(false_negative_counter.values())
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)

    for signature, count in false_negative_counter.items():
        failures.append(
            {"kind": "false negative", "detail": _format_signature(signature), "count": str(count)}
        )
    for signature, count in false_positive_counter.items():
        failures.append(
            {"kind": "false positive", "detail": _format_signature(signature), "count": str(count)}
        )

    review_tp = len(expected_review_keys & predicted_review_keys)
    review_fp = len(predicted_review_keys - expected_review_keys)
    review_fn = len(expected_review_keys - predicted_review_keys)
    review_precision = _safe_divide(review_tp, review_tp + review_fp)
    review_recall = _safe_divide(review_tp, review_tp + review_fn)
    document_review_correct = sum(
        (record.document_id in expected_review_documents)
        == (record.document_id in predicted_review_documents)
        for record in records
    )

    return {
        "documents": len(records),
        "expected_fields": sum(expected_counter.values()),
        "predicted_fields": sum(predicted_counter.values()),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "field_precision": precision,
        "field_recall": recall,
        "field_f1": _f1(precision, recall),
        "aligned_fields": aligned_fields,
        "normalized_value_accuracy": _safe_divide(normalized_correct, aligned_fields),
        "provenance_span_accuracy": _safe_divide(span_correct, aligned_fields),
        "review_precision": review_precision,
        "review_recall": review_recall,
        "review_f1": _f1(review_precision, review_recall),
        "document_review_accuracy": _safe_divide(document_review_correct, len(records)),
        "latency_ms_per_document": _safe_divide(extraction_seconds * 1000, len(records)),
        "failures": failures,
    }


def benchmark_extraction(
    gold_path: Path, *, confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
) -> dict[str, Any]:
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be between 0 and 1.")
    records = load_gold(gold_path)
    return {
        "extractor_version": EXTRACTOR_VERSION,
        "confidence_threshold": confidence_threshold,
        "documents_total": len(records),
        "evaluations": {
            split: _evaluate_records(
                [record for record in records if record.split == split],
                confidence_threshold=confidence_threshold,
            )
            for split in ("validation", "test")
        },
    }


def render_report(results: dict[str, Any], gold_path: Path) -> str:
    lines = [
        "# Phase 3 information-extraction benchmark",
        "",
        "_Reproducible: `rxauth-benchmark-extraction`._",
        "",
        "## Contract",
        f"- Gold dataset: `{gold_path.as_posix()}`",
        f"- Documents: {results['documents_total']}",
        f"- Extractor: `{results['extractor_version']}`",
        f"- Human-review threshold: {results['confidence_threshold']:.2f}",
        "- Gold source spans are hand-authored strings that must occur exactly once per document.",
        "- Validation and refreshed test are reported separately; exposed failures move to validation.",
        "- Dataset history and the test-refresh limitation are disclosed in `docs/extraction-gold.md`.",
        "- All documents and identities are synthetic; metrics do not establish production validity.",
        "",
        "## Results",
        "| Split | Documents | Gold fields | Predicted | Precision | Recall | F1 | "
        "Normalized accuracy | Span accuracy | Review F1 | Document review accuracy | "
        "Latency (ms/doc) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for split in ("validation", "test"):
        evaluation = results["evaluations"][split]
        lines.append(
            f"| {split} | {evaluation['documents']} | {evaluation['expected_fields']} | "
            f"{evaluation['predicted_fields']} | {evaluation['field_precision']:.3f} | "
            f"{evaluation['field_recall']:.3f} | {evaluation['field_f1']:.3f} | "
            f"{evaluation['normalized_value_accuracy']:.3f} | "
            f"{evaluation['provenance_span_accuracy']:.3f} | "
            f"{evaluation['review_f1']:.3f} | "
            f"{evaluation['document_review_accuracy']:.3f} | "
            f"{evaluation['latency_ms_per_document']:.3f} |"
        )

    for split in ("validation", "test"):
        lines += ["", f"## {split.capitalize()} failures"]
        failures = results["evaluations"][split]["failures"]
        if not failures:
            lines.append("None.")
            continue
        lines += ["| Kind | Count | Detail |", "|---|---:|---|"]
        for failure in failures[:30]:
            detail = failure["detail"].replace("|", "\\|")
            lines.append(f"| {failure['kind']} | {failure['count']} | {detail} |")

    lines += [
        "",
        "## Interpretation",
        "Exact field F1 requires the evidence type, normalized values, and cited source text to "
        "all agree. Normalized-value and span accuracy are calculated only for fields aligned "
        "by evidence type plus source text. Review metrics measure whether low-confidence fields "
        "are routed as specified by gold annotations.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark information extraction against gold JSONL."
    )
    parser.add_argument("--gold-path", type=Path, default=Path("data/extraction_gold.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--confidence-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    args = parser.parse_args()
    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0 and 1.")

    results = benchmark_extraction(args.gold_path, confidence_threshold=args.confidence_threshold)
    report = render_report(results, args.gold_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "extraction_benchmark.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
