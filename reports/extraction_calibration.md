# Phase 3 extraction confidence calibration

_Reproducible: `rxauth-calibrate-extraction`._

## Contract
- Gold dataset: `data/extraction_gold.jsonl`
- Split read: **validation only** (29 documents). The test split never tunes a confidence value or a review threshold.
- Extractor: `regex-v3`
- Fields scored: 30 (30 correct, 0 gold fields missed)
- A prediction with no gold counterpart counts as incorrect, not as unscored.
- All documents are synthetic and in-distribution; these numbers describe the rules against this corpus, not clinical text.

## Reliability by assigned confidence
| Assigned confidence | Fields | Correct | Observed accuracy | Gap | Rules |
|---:|---:|---:|---:|---:|---|
| 0.60 | 4 | 4 | 1.000 | +0.400 | previous_therapy_outcome_only, therapy_duration_vague |
| 0.75 | 1 | 1 | 1.000 | +0.250 | lab_a1c |
| 0.85 | 5 | 5 | 1.000 | +0.150 | previous_therapy_documented |
| 0.90 | 7 | 7 | 1.000 | +0.100 | additional_lab, prescription, previous_therapy_used_for, screening_documentation |
| 0.95 | 8 | 8 | 1.000 | +0.050 | days_supply, diagnosis, lab_a1c, payer_card_heading, payer_labeled, prescription_quantity |
| 0.98 | 5 | 5 | 1.000 | +0.020 | document_date, member_id, patient_id |

- Expected calibration error: **0.127**
- Brier score: **0.030**

## Accuracy by rule
| Rule | Fields | Mean confidence | Observed accuracy |
|---|---:|---:|---:|
| `previous_therapy_documented` | 5 | 0.850 | 1.000 |
| `additional_lab` | 4 | 0.900 | 1.000 |
| `document_date` | 3 | 0.980 | 1.000 |
| `previous_therapy_outcome_only` | 3 | 0.600 | 1.000 |
| `diagnosis` | 2 | 0.950 | 1.000 |
| `lab_a1c` | 2 | 0.850 | 1.000 |
| `payer_card_heading` | 2 | 0.950 | 1.000 |
| `days_supply` | 1 | 0.950 | 1.000 |
| `member_id` | 1 | 0.980 | 1.000 |
| `patient_id` | 1 | 0.980 | 1.000 |
| `payer_labeled` | 1 | 0.950 | 1.000 |
| `prescription` | 1 | 0.900 | 1.000 |
| `prescription_quantity` | 1 | 0.950 | 1.000 |
| `previous_therapy_used_for` | 1 | 0.900 | 1.000 |
| `screening_documentation` | 1 | 0.900 | 1.000 |
| `therapy_duration_vague` | 1 | 0.600 | 1.000 |

## Review-threshold sweep

Routing is no longer a pure threshold decision: an incomplete or ambiguously linked field is routed to review whatever the threshold is. The sweep therefore measures how much the threshold can move before it starts disagreeing with the gold routing.

| Threshold | Fields routed | Precision | Recall | F1 |
|---:|---:|---:|---:|---:|
| 0.50 | 4 | 1.000 | 1.000 | 1.000 |
| 0.55 | 4 | 1.000 | 1.000 | 1.000 |
| 0.60 | 4 | 1.000 | 1.000 | 1.000 |
| 0.65 **(current)** | 4 | 1.000 | 1.000 | 1.000 |
| 0.70 | 4 | 1.000 | 1.000 | 1.000 |
| 0.75 | 4 | 1.000 | 1.000 | 1.000 |
| 0.80 | 5 | 0.800 | 1.000 | 0.889 |
| 0.85 | 5 | 0.800 | 1.000 | 0.889 |
| 0.90 | 10 | 0.400 | 1.000 | 0.571 |
| 0.95 | 17 | 0.235 | 1.000 | 0.381 |

## Resolution stages on this split
| Stage | Count |
|---|---:|
| Facts citing more than one span | 2 |
| Spans suppressed during overlap resolution | 0 |

## Reading these numbers

The gap column is positive across every bucket: on this corpus the rules are correct more often than they claim to be. That is the expected direction for hand-set priors chosen to be conservative, and it is not evidence that the priors should be raised. Each bucket holds a handful of fields drawn from documents written in the same vocabulary the rules target, so fitting a mapping to these observations would encode the sample rather than calibrate the extractor. The values stay as documented priors until the calibration split contains independently authored paraphrases and OCR-degraded pages.

The sweep shows the same thing about the review threshold. Its useful reading is the width of the band that reproduces the gold routing, not the single best value inside it: the band is 0.50–0.75 at F1 1.000, and the configured default of 0.65 sits inside it. A threshold that had to be tuned to a precise value to work would be a warning sign, not a result.

The most useful finding is structural rather than numerical. Fields at the lowest confidence bucket are *correct*, and they are still the ones a reviewer must see — they are read accurately and simply do not state enough for a deterministic check. One number cannot carry both meanings, which is why routing now distinguishes `low_confidence` (the span may have been misread) from `incomplete_value` and `ambiguous_linkage` (the span was read correctly and still needs a human).
