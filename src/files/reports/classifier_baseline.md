# Classifier baseline — TF-IDF + Logistic Regression

_Main README §8, Phase 1. Reproducible: `python data/build_dataset.py` then `python train_classifier_baseline.py`._

## Dataset
- Source: `D:/Cloud/DevLap/Projects/RxAuth AI/files/src/files/data/manifest.csv`
- Train / val / test sizes: 336 / 72 / 72
- Classes (8): clinical_note, insurance_card, lab_report, medication_history, other, pa_request, prescription, referral
- All documents are template-generated synthetic text (main README §3 guardrail) — no real patient, provider, or payer data.
- The vectorizer is fit on the train split only; val/test text is never seen during fitting (leakage check).

## Headline metrics
- Train accuracy: 0.991
- Test accuracy: 0.986
- Train/test accuracy gap: 0.005 (no evidence of overfitting)
- Inference latency: 0.418 ms/document (single-document predict, CPU, includes vectorization)

## Per-class precision / recall / F1
```
                    precision    recall  f1-score   support

     clinical_note      1.000     1.000     1.000         9
    insurance_card      1.000     1.000     1.000         9
        lab_report      1.000     1.000     1.000         9
medication_history      1.000     1.000     1.000         9
             other      1.000     1.000     1.000         9
        pa_request      0.900     1.000     0.947         9
      prescription      1.000     1.000     1.000         9
          referral      1.000     0.889     0.941         9

          accuracy                          0.986        72
         macro avg      0.988     0.986     0.986        72
      weighted avg      0.987     0.986     0.986        72
```

## Confusion matrix (rows = true label, columns = predicted label)
| true \ pred | clinical_note | insurance_card | lab_report | medication_history | other | pa_request | prescription | referral |
|---|---|---|---|---|---|---|---|---|
| clinical_note | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| insurance_card | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 |
| lab_report | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 |
| medication_history | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 |
| other | 0 | 0 | 0 | 0 | 9 | 0 | 0 | 0 |
| pa_request | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 0 |
| prescription | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 0 |
| referral | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 8 |

## Failure cases
| file | true | predicted | text snippet |
|---|---|---|---|
| doc_0029.txt | referral | pa_request | Referring provider: Dr. S. Ibrahim. Referred to: Specialty Clinic. Contact the office with... |

## Known limitation
This dataset is template-generated synthetic text, not real scanned/OCR'd documents. These numbers validate the pipeline and evaluation methodology end to end — they are not a claim about real-world generalization (main README §3, no-fabricated-metrics guardrail). Phase 2 (§8) compares a deep model against this same methodology on the same dataset contract.
