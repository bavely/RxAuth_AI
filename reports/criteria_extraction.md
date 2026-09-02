# Phase 4 policy criteria-extraction benchmark

_Reproducible: `rxauth-benchmark-criteria`._

## Contract
- Policy corpus: `data/policies/` (8 policy versions)
- Gold criteria: `data/policy_criteria_gold.jsonl`
- Extractor: `policy-rules-v1`
- Review threshold: 0.65
- A criterion is correct only when its type, medication, operator, threshold, unit, required outcome, **and** quoted source text all agree with the gold.
- Every policy version in the corpus must have a gold record; the loader refuses a partial dataset.
- Gold source text must occur exactly once in the policy it names.
- The corpus is synthetic public-style policy text. These numbers describe the rules against this corpus, not real payer publications.

## Results
| Metric | Value |
|---|---:|
| Gold criteria | 32 |
| Extracted criteria | 32 |
| Criterion precision | 1.000 |
| Criterion recall | 1.000 |
| Criterion F1 | 1.000 |
| Provenance-span accuracy | 1.000 |
| Connective accuracy | 1.000 |
| Exclusion-count accuracy | 1.000 |
| Unstructured-requirement recall | 1.000 (1 expected) |
| Latency (ms/policy) | 0.069 |

## Per policy
| Policy version | Gold | Extracted | Matched | Connective | Exclusions | Unstructured |
|---|---:|---:|---:|---|---|---:|
| `PA-104:2026-01` | 6 | 6 | 6 | all | 2 | 0 |
| `PA-104:2024-06` | 3 | 3 | 3 | all | 0 | 0 |
| `PA-207:2025-09` | 4 | 4 | 4 | all | 0 | 0 |
| `PA-118:2025-03` | 4 | 4 | 4 | all | 1 | 0 |
| `PA-233:2025-07` | 5 | 5 | 5 | all | 0 | 0 |
| `PA-341:2026-02` | 2 | 2 | 2 | any | 0 | 0 |
| `PA-402:2025-11` | 4 | 4 | 4 | all | 2 | 1 |
| `PA-509:2025-05` | 4 | 4 | 4 | all | 0 | 0 |

## Failures
None.

## Interpretation

The score says the rules read this corpus correctly. It does not say they read payer prose correctly: the sentences were authored locally in the forms the rules expect, so this measures the declared contract — normalization, provenance, connective detection, and the routing of what could not be structured — not generalization to real policy language.

The unstructured row is the one to watch as the corpus grows. A rule set that silently drops requirements it cannot parse will keep a perfect criterion F1 while shrinking the policy a case is judged against, which is the more dangerous error of the two: the case reads as readier than it is. Recall over the gold's unstructured items is what makes that failure visible.
