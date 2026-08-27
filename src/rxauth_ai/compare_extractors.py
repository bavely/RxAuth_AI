"""Reproducible deterministic-vs-learned extraction comparison.

The learned candidate is intentionally small: token-level multinomial logistic
regression using lexical and local-context features. It predicts evidence span
boundaries and types, which is enough to test whether learning improves the
hardest part of the deterministic extractor without pretending it can yet
produce normalized, provenance-complete ``Evidence`` records.
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression

from .benchmark_extraction import GoldDocument, load_gold
from .extraction import EXTRACTOR_VERSION, extract_evidence
from .ingestion import IngestedDocument

LEARNED_EXTRACTOR_VERSION = "token-logreg-v1"
RANDOM_SEED = 42
LEARNED_TRAINING_DOCUMENTS = 20
_TOKEN_PATTERN = re.compile(r"\w+(?:[-_]\w+)*|[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class PredictedSpan:
    evidence_type: str
    page: int
    start: int
    end: int
    source_text: str


def _tokens(text: str) -> list[Token]:
    return [Token(match.group(), match.start(), match.end()) for match in _TOKEN_PATTERN.finditer(text)]


def _shape(token: str) -> str:
    return re.sub(r"[A-Z]", "X", re.sub(r"[a-z]", "x", re.sub(r"\d", "d", token)))


def _features(tokens: list[Token], index: int) -> dict[str, str | bool]:
    token = tokens[index].text
    features: dict[str, str | bool] = {
        "bias": True,
        "token": token.casefold(),
        "shape": _shape(token),
        "prefix2": token[:2].casefold(),
        "suffix3": token[-3:].casefold(),
        "is_digit": token.replace(".", "", 1).isdigit(),
        "is_title": token.istitle(),
        "is_upper": token.isupper(),
    }
    if index == 0:
        features["BOS"] = True
    else:
        features["previous"] = tokens[index - 1].text.casefold()
    if index == len(tokens) - 1:
        features["EOS"] = True
    else:
        features["next"] = tokens[index + 1].text.casefold()
    return features


def _labels(record: GoldDocument, page_number: int, tokens: list[Token]) -> list[str]:
    labels = ["O"] * len(tokens)
    page_text = record.text_for_page(page_number)
    for expected in (item for item in record.expected if item.page == page_number):
        start = page_text.index(expected.source_text)
        end = start + len(expected.source_text)
        indexes = [
            index
            for index, token in enumerate(tokens)
            if token.start >= start and token.end <= end
        ]
        for offset, token_index in enumerate(indexes):
            prefix = "B" if offset == 0 else "I"
            labels[token_index] = f"{prefix}-{expected.evidence_type}"
    return labels


class LearnedSpanExtractor:
    """Token classifier trained only from the explicitly supplied records."""

    def __init__(self) -> None:
        self.vectorizer = DictVectorizer(sparse=True)
        self.model = LogisticRegression(
            class_weight="balanced",
            max_iter=500,
            random_state=RANDOM_SEED,
        )
        self.training_document_ids: tuple[str, ...] = ()

    def fit(self, records: list[GoldDocument]) -> LearnedSpanExtractor:
        features: list[dict[str, str | bool]] = []
        labels: list[str] = []
        for record in records:
            for page in record.ingested_pages():
                tokens = _tokens(page.text)
                features.extend(_features(tokens, index) for index in range(len(tokens)))
                labels.extend(_labels(record, page.page_number, tokens))
        self.model.fit(self.vectorizer.fit_transform(features), labels)
        self.training_document_ids = tuple(record.document_id for record in records)
        return self

    def predict(self, record: GoldDocument) -> list[PredictedSpan]:
        spans: list[PredictedSpan] = []
        for page in record.ingested_pages():
            tokens = _tokens(page.text)
            if not tokens:
                continue
            labels = self.model.predict(
                self.vectorizer.transform(
                    [_features(tokens, index) for index in range(len(tokens))]
                )
            )
            current_type: str | None = None
            current_start = 0
            current_end = 0
            for index, label in enumerate(labels):
                if label == "O":
                    if current_type is not None:
                        spans.append(
                            PredictedSpan(
                                current_type,
                                page.page_number,
                                current_start,
                                current_end,
                                page.text[current_start:current_end],
                            )
                        )
                        current_type = None
                    continue
                prefix, evidence_type = str(label).split("-", 1)
                if current_type is not None and (prefix == "B" or evidence_type != current_type):
                    spans.append(
                        PredictedSpan(
                            current_type,
                            page.page_number,
                            current_start,
                            current_end,
                            page.text[current_start:current_end],
                        )
                    )
                    current_type = None
                if current_type is None:
                    current_type = evidence_type
                    current_start = tokens[index].start
                current_end = tokens[index].end
            if current_type is not None:
                spans.append(
                    PredictedSpan(
                        current_type,
                        page.page_number,
                        current_start,
                        current_end,
                        page.text[current_start:current_end],
                    )
                )
        return spans


def _gold_keys(record: GoldDocument) -> set[tuple[str, int, int, int]]:
    keys: set[tuple[str, int, int, int]] = set()
    for expected in record.expected:
        start = record.text_for_page(expected.page).index(expected.source_text)
        keys.add((expected.evidence_type, expected.page, start, start + len(expected.source_text)))
    return keys


def _metrics(
    records: list[GoldDocument], predictor: Any
) -> dict[str, float | int | list[dict[str, str]]]:
    true_positive = false_positive = false_negative = 0
    failures: list[dict[str, str]] = []
    started = time.perf_counter()
    for record in records:
        expected = _gold_keys(record)
        predicted = {
            (span.evidence_type, span.page, span.start, span.end) for span in predictor(record)
        }
        true_positive += len(expected & predicted)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)
        for key in sorted(expected - predicted):
            failures.append({"document_id": record.document_id, "kind": "false negative", "span": repr(key)})
        for key in sorted(predicted - expected):
            failures.append({"document_id": record.document_id, "kind": "false positive", "span": repr(key)})
    elapsed = time.perf_counter() - started
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "documents": len(records),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "latency_ms_per_document": elapsed * 1000 / len(records) if records else 0.0,
        "failures": failures,
    }


def _rule_predict(record: GoldDocument) -> list[PredictedSpan]:
    result = extract_evidence(
        IngestedDocument(filename=record.filename, media_type="text", pages=record.ingested_pages()),
        document_id=record.document_id,
    )
    return [
        PredictedSpan(
            item.evidence_type,
            item.provenance.page or 1,
            item.provenance.start_char or 0,
            item.provenance.end_char or 0,
            item.provenance.source_text or "",
        )
        for item in result.evidence
    ]


def compare_extractors(gold_path: Path) -> dict[str, Any]:
    records = load_gold(gold_path)
    development = [record for record in records if record.split == "validation"]
    if len(development) <= LEARNED_TRAINING_DOCUMENTS:
        raise ValueError(
            "Learned comparison needs more validation records than its fixed training allocation."
        )
    training = development[:LEARNED_TRAINING_DOCUMENTS]
    selection = development[LEARNED_TRAINING_DOCUMENTS:]
    learned = LearnedSpanExtractor().fit(training)
    evaluations: dict[str, dict[str, Any]] = {}
    evaluation_records = {
        "training": training,
        "validation": selection,
        "test": [record for record in records if record.split == "test"],
        "challenge": [record for record in records if record.split == "challenge"],
    }
    for split, split_records in evaluation_records.items():
        if not split_records:
            continue
        evaluations[split] = {
            "rules": _metrics(split_records, _rule_predict),
            "learned": _metrics(split_records, learned.predict),
        }
    rules_win = evaluations["validation"]["rules"]["f1"] >= evaluations["validation"]["learned"]["f1"]
    return {
        "rule_extractor": EXTRACTOR_VERSION,
        "learned_extractor": LEARNED_EXTRACTOR_VERSION,
        "random_seed": RANDOM_SEED,
        "training_document_ids": list(learned.training_document_ids),
        "selection_document_ids": [record.document_id for record in selection],
        "evaluations": evaluations,
        "selected_extractor": EXTRACTOR_VERSION if rules_win else LEARNED_EXTRACTOR_VERSION,
    }


def render_report(results: dict[str, Any], gold_path: Path) -> str:
    lines = [
        "# Phase 3 learned extraction comparison",
        "",
        "_Reproducible: `rxauth-compare-extractors`._",
        "",
        "## Protocol",
        "",
        f"- Dataset: `{gold_path.as_posix()}`",
        f"- Deterministic candidate: `{results['rule_extractor']}`",
        f"- Learned candidate: `{results['learned_extractor']}` (seed {results['random_seed']})",
        f"- Learned training records: {len(results['training_document_ids'])} fixed development documents.",
        f"- Model-selection records: {len(results['selection_document_ids'])} remaining validation documents.",
        "- Test and challenge labels are never used for fitting or model selection.",
        "- Metric: exact evidence-type + page + start/end span precision, recall, and F1.",
        "- The training row is a fit diagnostic; the validation row makes the selection decision.",
        "- The challenge slice is synthetic and locally authored; it is harder coverage, not an externally independent clinical benchmark.",
        "",
        "## Results",
        "",
        "| Split | Candidate | Documents | Precision | Recall | F1 | Latency (ms/doc) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for split, candidates in results["evaluations"].items():
        for candidate, metrics in candidates.items():
            lines.append(
                f"| {split} | {candidate} | {metrics['documents']} | "
                f"{metrics['precision']:.3f} | {metrics['recall']:.3f} | "
                f"{metrics['f1']:.3f} | {metrics['latency_ms_per_document']:.3f} |"
            )
    lines += [
        "",
        "## Decision",
        "",
        f"Selected extractor: `{results['selected_extractor']}`.",
        "",
        "The learned candidate detects spans only; it does not yet provide normalized values, issue kinds, overlap suppression, or multi-span provenance. It would need a material held-out robustness gain before that additional complexity could replace the complete deterministic contract.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare rule and learned extraction candidates.")
    parser.add_argument("--gold-path", type=Path, default=Path("data/extraction_gold.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    results = compare_extractors(args.gold_path)
    report = render_report(results, args.gold_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "extraction_learned_comparison.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
