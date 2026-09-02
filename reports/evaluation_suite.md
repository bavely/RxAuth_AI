# Evaluation suite

_Reproducible: `rxauth-evaluate`._

## Contract

- Suite: `eval-suite-v1`
- Metrics: 22 across 6 layers
- Thresholds are a ratchet set at the values the current code produces. A regression fails the build; relaxing a threshold is a deliberate, reviewable diff.
- Every gold set is synthetic and authored in this repository. These numbers validate the declared contracts, not clinical or production generalization.

**Result: PASS** (22/22 within threshold)

## Scorecard

| Layer | Metric | Value | Threshold | Status |
|---|---|---:|---:|:--|
| classification | test macro F1 | 0.979 | >= 0.950 | pass |
| classification | challenge macro F1 | 0.916 | >= 0.880 | pass |
| extraction | test field F1 | 1.000 | >= 1.000 | pass |
| extraction | test provenance span accuracy | 1.000 | >= 1.000 | pass |
| extraction | challenge field F1 | 1.000 | >= 1.000 | pass |
| extraction | challenge provenance span accuracy | 1.000 | >= 1.000 | pass |
| retrieval | correct-policy rate | 1.000 | >= 1.000 | pass |
| retrieval | advantage over vector-only | 0.375 | >= 0.300 | pass |
| retrieval | declined when it should | 1.000 | >= 1.000 | pass |
| criteria | criterion F1 | 1.000 | >= 1.000 | pass |
| criteria | provenance accuracy | 1.000 | >= 1.000 | pass |
| criteria | connective accuracy | 1.000 | >= 1.000 | pass |
| criteria | unstructured recall | 1.000 | >= 1.000 | pass |
| matching | test result accuracy | 1.000 | >= 1.000 | pass |
| matching | test evidence F1 | 1.000 | >= 1.000 | pass |
| matching | test false-support rate | 0.000 | <= 0.000 | pass |
| matching | challenge result accuracy | 1.000 | >= 1.000 | pass |
| matching | challenge evidence F1 | 1.000 | >= 1.000 | pass |
| matching | challenge false-support rate | 0.000 | <= 0.000 | pass |
| generation | unsupported-claim rate | 0.000 | <= 0.000 | pass |
| generation | claims carrying a citation | 1.000 | >= 1.000 | pass |
| generation | gate passed | 1.000 | >= 1.000 | pass |

## Breaches

None.
