# Evaluation, reports, and gold data (README §3, §15)

`reports/` is published evidence. CI's `reports` job regenerates every report and fails
on drift (`rxauth-check-reports`), and `rxauth-evaluate` gates each layer on a threshold.

## Never hand-edit
- `reports/**`: regenerate with the producing command (`/regenerate-reports`).
- `data/*_gold.jsonl`: hand-labelled answer keys. Changing a label to make a
  benchmark pass defeats the benchmark. If a label really is wrong, stop and tell the
  user. Don't change it in the same change as the code it scores.
- `data/manifest.csv`, `data/ingestion_manifest.csv`, `data/documents/**`,
  `data/rendered/**`: produced by `rxauth-build-dataset`. Hand edits risk split leakage.

The `protect-paths` hook enforces this.

## Thresholds are a ratchet
- `evaluation_suite.py` thresholds sit at what the current code produces. Raise them
  when a change improves a metric. Lowering one needs an explicit reason in the
  commit message, and you need the user's agreement first.

## Versions
When behaviour changes, bump the constant that stamps the result so reports stay
attributable:

| Module | Constant |
|---|---|
| `extraction.py` | `EXTRACTOR_VERSION` |
| `matching.py` | `MATCHER_VERSION`, `NORMALIZATION_VERSION` |
| `criteria_extraction.py` | `CRITERIA_EXTRACTOR_VERSION` |
| `policy_corpus.py` | `CORPUS_VERSION` |
| `generation.py` | `GENERATOR_VERSION` |
| `workflow.py` | `WORKFLOW_VERSION` |
| `registry.py` | `ARTIFACT_FORMAT_VERSION` |
| `feedback.py` | `FEEDBACK_SCHEMA_VERSION` |

## Which benchmark covers what
- Ingestion/OCR → `rxauth-benchmark-ingestion`
- Extraction rules → `rxauth-benchmark-extraction`, `rxauth-calibrate-extraction`, `rxauth-compare-extractors`
- Policy chunking/retrieval → `rxauth-benchmark-retrieval` (**and** criteria, since chunks feed both)
- Criteria extraction → `rxauth-benchmark-criteria`
- Matching/units → `rxauth-benchmark-matching`, then `rxauth-run-case data/cases/PA-CASE-001`
- Anything in the spine → all of the above + `rxauth-evaluate`

Latency cells are excluded from drift checks. Every quality number must reproduce exactly.
