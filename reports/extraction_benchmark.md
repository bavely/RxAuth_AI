# Phase 3 information-extraction benchmark

_Reproducible: `rxauth-benchmark-extraction`._

## Contract
- Gold dataset: `data/extraction_gold.jsonl`
- Documents: 61
- Extractor: `regex-v3`
- Human-review threshold: 0.65
- Gold source spans are hand-authored strings that must occur exactly once per document.
- Validation, refreshed test, and challenge are reported separately; exposed test failures move to validation.
- Dataset history and the test-refresh limitation are disclosed in `docs/extraction-gold.md`.
- All documents and identities are synthetic; metrics do not establish production validity.

## Results
| Split | Documents | Gold fields | Predicted | Precision | Recall | F1 | Normalized accuracy | Span accuracy | Review F1 | Document review accuracy | Latency (ms/doc) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| validation | 29 | 30 | 30 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.051 |
| test | 20 | 25 | 25 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.051 |
| challenge | 12 | 11 | 11 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.054 |

## Validation failures
None.

## Test failures
None.

## Challenge failures
None.

## Interpretation
Exact field F1 requires the evidence type, normalized values, and cited source text to all agree. Normalized-value and span accuracy are calculated only for fields aligned by evidence type, page, and source text. Review metrics measure whether low-confidence fields are routed as specified by gold annotations.
