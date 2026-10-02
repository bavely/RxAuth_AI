---
name: regenerate-reports
description: Regenerate RxAuth AI's committed evaluation reports in CI order, score them against thresholds, and check for drift. Use after any change to the extraction/policy/matching/workflow spine, when rxauth-check-reports or rxauth-evaluate fails, or when the user asks to refresh reports/.
---

# Regenerate reports

`reports/` is evidence (README §3). It is only ever written by these commands, never by
hand. This mirrors the `reports` job in `.github/workflows/ci.yml`. Keep the order:
later steps read earlier outputs.

## 1. Preconditions
```bash
uv sync --group dev
git status --short reports/      # note what was already dirty before you start
```

## 2. Classifier artifact (gitignored, needed by run-case)
```bash
uv run rxauth-train-classifier
```

## 3. Flagship case
```bash
uv run rxauth-run-case data/cases/PA-CASE-001
```

## 4. Every benchmark
```bash
uv run rxauth-benchmark-ingestion
uv run rxauth-benchmark-extraction
uv run rxauth-benchmark-retrieval
uv run rxauth-benchmark-criteria
uv run rxauth-benchmark-matching
uv run rxauth-calibrate-extraction
uv run rxauth-compare-extractors
```

## 5. Gate
```bash
uv run rxauth-evaluate         # non-zero if any metric falls below its threshold
uv run rxauth-check-reports    # non-zero if a committed report does not reproduce
```

`rxauth-check-reports` compares against the **committed** blob, so before committing it
reports the diff you're about to commit. That is expected.

## 6. Report back
- `git diff --stat reports/` and the metric lines that moved in
  `reports/evaluation_suite.md` (before → after).
- If `rxauth-evaluate` failed: name the metric and the change that caused it. **Do not**
  lower a threshold in `evaluation_suite.py` or edit `data/*_gold.jsonl` to recover.
  Fix the regression, or ask the user whether the trade-off is intended.
- If a metric improved, point out that the threshold ratchet could be raised to match.
- Timing-only diffs are noise (normalized by the checker). Don't comment on them.

Skip `rxauth-train-deep-classifier`: it needs the `deep` extra and its report is
outside `check_reports.DEFAULT_REPORTS`.
