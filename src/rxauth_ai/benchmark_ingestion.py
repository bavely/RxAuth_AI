"""Reproducible evaluation for the Phase 1.5 ingestion boundary."""

from __future__ import annotations

import argparse
import csv
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .ingestion import ingest_document, preprocess_image


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(expected: str, actual: str) -> float:
    expected = _normalize(expected)
    actual = _normalize(actual)
    if not expected:
        return 0.0 if not actual else 1.0
    return _edit_distance(expected, actual) / len(expected)


def benchmark_ingestion(
    data_dir: Path,
    *,
    ocr_backend: Callable[[Any], tuple[str, float]] | None = None,
    run_ocr: bool = False,
) -> dict[str, Any]:
    manifest_path = data_dir / "ingestion_manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} not found — rebuild the synthetic dataset.")

    with manifest_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(
            "Ingestion manifest is empty; rebuild with --rendered-per-class above zero."
        )

    pdf_error_rates: list[float] = []
    image_error_rates: list[float] = []
    image_preprocessing_failures: list[str] = []
    for row in rows:
        asset_path = data_dir / row["asset_relative_path"]
        expected = (data_dir / row["ground_truth_relative_path"]).read_text(encoding="utf-8")
        if row["source_format"] == "pdf":
            actual = ingest_document(asset_path).text
            pdf_error_rates.append(character_error_rate(expected, actual))
        elif row["source_format"] == "image":
            try:
                preprocess_image(asset_path)
            except Exception:  # noqa: BLE001 - retain filenames for benchmark diagnosis
                image_preprocessing_failures.append(row["asset_relative_path"])
                continue
            if ocr_backend is not None or run_ocr:
                actual = ingest_document(asset_path, ocr_backend=ocr_backend).text
                image_error_rates.append(character_error_rate(expected, actual))
        else:
            raise ValueError(f"Unknown ingestion format: {row['source_format']!r}")

    image_count = sum(row["source_format"] == "image" for row in rows)
    return {
        "documents_total": len(rows),
        "pdf_documents": len(pdf_error_rates),
        "pdf_mean_character_error_rate": (
            sum(pdf_error_rates) / len(pdf_error_rates) if pdf_error_rates else None
        ),
        "image_documents": image_count,
        "image_preprocessing_success_rate": (
            (image_count - len(image_preprocessing_failures)) / image_count if image_count else None
        ),
        "image_preprocessing_failures": image_preprocessing_failures,
        "ocr_documents": len(image_error_rates),
        "ocr_mean_character_error_rate": (
            sum(image_error_rates) / len(image_error_rates) if image_error_rates else None
        ),
    }


def render_report(results: dict[str, Any]) -> str:
    ocr_metric = results["ocr_mean_character_error_rate"]
    pdf_metric = results["pdf_mean_character_error_rate"]
    preprocessing_metric = results["image_preprocessing_success_rate"]
    lines = [
        "# Phase 1.5 ingestion benchmark",
        "",
        "_Reproducible: `rxauth-build-dataset` then `rxauth-benchmark-ingestion`._",
        "",
        "## Corpus",
        f"- Rendered assets: {results['documents_total']}",
        f"- Text-bearing PDFs: {results['pdf_documents']}",
        f"- Scan-like images: {results['image_documents']}",
        "- Data: fully synthetic; no PHI",
        "",
        "## Results",
        "- PDF mean character error rate: "
        + (f"{pdf_metric:.3f}" if pdf_metric is not None else "not measured"),
        "- Image preprocessing success rate: "
        + (f"{preprocessing_metric:.1%}" if preprocessing_metric is not None else "not measured"),
    ]
    if ocr_metric is None:
        lines += [
            "- Image OCR character error rate: not measured",
            "",
            "Image rendering and OpenCV normalization are covered. OCR text accuracy remains "
            "explicitly unreported until an OCR runtime is configured; the benchmark accepts "
            "an injected backend and the default ingestion path supports optional Tesseract.",
        ]
    else:
        lines.append(f"- Image OCR mean character error rate: {ocr_metric:.3f}")
    lines += [
        "",
        "Character error rate is Levenshtein edit distance divided by normalized ground-truth "
        "length; lower is better.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate rendered document ingestion.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument(
        "--run-ocr",
        action="store_true",
        help="Measure image OCR with the optional Tesseract backend.",
    )
    args = parser.parse_args()

    results = benchmark_ingestion(args.data_dir, run_ocr=args.run_ocr)
    report = render_report(results)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "ingestion_benchmark.md"
    path.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"Report written to: {path}")


if __name__ == "__main__":
    main()
