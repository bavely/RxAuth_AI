# Phase 5 criteria-to-evidence matching benchmark

_Reproducible: `rxauth-benchmark-matching`._

## Contract

- Gold dataset: `data/matching_gold.jsonl` (42 matches)
- Matcher: `evidence-match-v2`
- Normalization: `clinical-units-v1`
- Exact evidence IDs are scored alongside the five-state result; the right status with the wrong source is a failure.
- The default ambiguity interpreter abstains. No model-generated value is present in these results.
- All cases are locally authored and synthetic; metrics validate this contract, not clinical generalization.

## Results

| Split | Matches | Result accuracy | Macro F1 | Evidence F1 | Retrieval recall | False support | Missing recall | Ambiguity recall | Review recall | Citation accuracy | Latency (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| validation | 15 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.089 |
| test | 14 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.053 |
| challenge | 13 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.060 |

## Validation failures
None.

## Test failures
None.

## Challenge failures
None.

## Interpretation

The benchmark is designed around unsafe shortcuts: selecting only the highest-confidence fact, accepting the wrong diagnosis, approximating calendar months, silently choosing one side of contradictory evidence, or treating an abstention as missing. False-support rate is reported explicitly because an unsupported SATISFIED result is the most dangerous matching error.
