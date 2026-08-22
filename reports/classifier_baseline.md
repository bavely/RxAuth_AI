# Phase 1.5 classifier benchmark — TF-IDF + Logistic Regression

_Reproducible: `rxauth-build-dataset` then `rxauth-train-classifier`._

## Dataset contract
- Source: `data/manifest.csv`
- Train / val / test / challenge sizes: 336 / 48 / 48 / 48
- Classes (8): clinical_note, insurance_card, lab_report, medication_history, other, pa_request, prescription, referral
- Cases and template families are mutually exclusive across every split.
- Challenge documents use an unseen layout family, cross-class noise, and deterministic OCR-like character corruption.
- All content is synthetic; these metrics do not claim clinical or production validity.

## Training
- Train accuracy: 0.994
- Vectorizer fit: train split only
- Confidence threshold for human review: 0.65

## Val metrics
- Accuracy: 0.938
- Macro F1: 0.936
- Mean confidence: 0.614
- Expected calibration error (10 bins): 0.323
- Human-review routing rate: 45.8%
- Batch inference latency: 0.004 ms/document (CPU)

```
                    precision    recall  f1-score   support

     clinical_note      1.000     1.000     1.000         6
    insurance_card      1.000     1.000     1.000         6
        lab_report      1.000     0.833     0.909         6
medication_history      1.000     0.667     0.800         6
             other      1.000     1.000     1.000         6
        pa_request      0.750     1.000     0.857         6
      prescription      0.857     1.000     0.923         6
          referral      1.000     1.000     1.000         6

          accuracy                          0.938        48
         macro avg      0.951     0.938     0.936        48
      weighted avg      0.951     0.938     0.936        48
```

## Test metrics
- Accuracy: 0.979
- Macro F1: 0.979
- Mean confidence: 0.561
- Expected calibration error (10 bins): 0.418
- Human-review routing rate: 68.8%
- Batch inference latency: 0.004 ms/document (CPU)

```
                    precision    recall  f1-score   support

     clinical_note      1.000     1.000     1.000         6
    insurance_card      1.000     1.000     1.000         6
        lab_report      1.000     1.000     1.000         6
medication_history      1.000     0.833     0.909         6
             other      1.000     1.000     1.000         6
        pa_request      0.857     1.000     0.923         6
      prescription      1.000     1.000     1.000         6
          referral      1.000     1.000     1.000         6

          accuracy                          0.979        48
         macro avg      0.982     0.979     0.979        48
      weighted avg      0.982     0.979     0.979        48
```

## Challenge metrics
- Accuracy: 0.917
- Macro F1: 0.916
- Mean confidence: 0.560
- Expected calibration error (10 bins): 0.357
- Human-review routing rate: 68.8%
- Batch inference latency: 0.004 ms/document (CPU)

```
                    precision    recall  f1-score   support

     clinical_note      1.000     1.000     1.000         6
    insurance_card      0.750     1.000     0.857         6
        lab_report      1.000     1.000     1.000         6
medication_history      1.000     1.000     1.000         6
             other      0.857     1.000     0.923         6
        pa_request      0.800     0.667     0.727         6
      prescription      1.000     0.833     0.909         6
          referral      1.000     0.833     0.909         6

          accuracy                          0.917        48
         macro avg      0.926     0.917     0.916        48
      weighted avg      0.926     0.917     0.916        48
```

## Test confusion matrix (rows = true label, columns = predicted label)
| true \ pred | clinical_note | insurance_card | lab_report | medication_history | other | pa_request | prescription | referral |
|---|---|---|---|---|---|---|---|---|
| clinical_note | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| insurance_card | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0 |
| lab_report | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 |
| medication_history | 0 | 0 | 0 | 5 | 0 | 1 | 0 | 0 |
| other | 0 | 0 | 0 | 0 | 6 | 0 | 0 | 0 |
| pa_request | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 |
| prescription | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 |
| referral | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 |

## Challenge failure cases
| file | true | predicted | text snippet |
|---|---|---|---|
| documents/pa_request/doc_0019.txt | pa_request | insurance_card | SCANNED CORRESPONDENCE Plcase retain a copy of this document for your records. Contact the... |
| documents/pa_request/doc_0029.txt | pa_request | other | SCANNED CORRESPONDENCE Contact the office with any questions regarding this documentation.... |
| documents/referral/doc_0029.txt | referral | pa_request | SCANNED CORRESPONDENCE Referring provider: Dr. S. Ibrahim. Referred to: Specialty Clinic.... |
| documents/prescription/doc_0049.txt | prescription | insurance_card | SCANNED CORRESPONDENCC Contact the office with any questions regarding this documentation.... |

## Interpretation
The grouped test set is the primary Phase 1.5 comparison point. The challenge set is deliberately harder and should be used for robustness/error analysis, not model selection. The rendered PDF/image corpus separately validates the ingestion boundary; image OCR quality depends on the configured OCR backend.
