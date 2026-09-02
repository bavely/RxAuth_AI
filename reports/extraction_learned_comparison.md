# Phase 3 learned extraction comparison

_Reproducible: `rxauth-compare-extractors`._

## Protocol

- Dataset: `data/extraction_gold.jsonl`
- Deterministic candidate: `regex-v3`
- Learned candidate: `token-logreg-v1` (seed 42)
- Learned training records: 20 fixed development documents.
- Model-selection records: 9 remaining validation documents.
- Test and challenge labels are never used for fitting or model selection.
- Metric: exact evidence-type + page + start/end span precision, recall, and F1.
- The training row is a fit diagnostic; the validation row makes the selection decision.
- The challenge slice is synthetic and locally authored; it is harder coverage, not an externally independent clinical benchmark.

## Results

| Split | Candidate | Documents | Precision | Recall | F1 | Latency (ms/doc) |
|---|---|---:|---:|---:|---:|---:|
| training | rules | 20 | 1.000 | 1.000 | 1.000 | 0.051 |
| training | learned | 20 | 0.385 | 0.833 | 0.526 | 0.293 |
| validation | rules | 9 | 1.000 | 1.000 | 1.000 | 0.083 |
| validation | learned | 9 | 0.044 | 0.167 | 0.070 | 0.312 |
| test | rules | 20 | 1.000 | 1.000 | 1.000 | 0.056 |
| test | learned | 20 | 0.053 | 0.200 | 0.084 | 0.279 |
| challenge | rules | 12 | 1.000 | 1.000 | 1.000 | 0.058 |
| challenge | learned | 12 | 0.000 | 0.000 | 0.000 | 0.296 |

## Decision

Selected extractor: `regex-v3`.

The learned candidate detects spans only; it does not yet provide normalized values, issue kinds, overlap suppression, or multi-span provenance. It would need a material held-out robustness gain before that additional complexity could replace the complete deterministic contract.
