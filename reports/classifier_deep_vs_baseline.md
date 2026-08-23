# Phase 2 classifier comparison — transformer vs classical baseline

_Reproducible: `uv sync --extra deep --group dev`, then `uv run rxauth-train-deep-classifier`._

## Experiment contract
- Source: `data/manifest.csv`
- Train / val / test / challenge sizes: 336 / 48 / 48 / 48
- Both models use the same case- and template-family-isolated splits.
- Transformer checkpoint selection and early stopping use validation macro F1 only.
- Test is the held-out comparison; challenge is robustness analysis only.
- All content is synthetic; results do not establish clinical or production validity.

## Training configuration
- Pretrained model: `distilbert-base-uncased`
- Seeds run: 7, 42, 73
- Selected artifact seed: 7 (highest validation macro F1)
- Device: cpu
- Hardware: Intel64 Family 6 Model 170 Stepping 4, GenuineIntel
- Epochs completed / selected: 4 / 4
- Batch size / max tokens: 16 / 256
- Learning rate / weight decay: 2e-05 / 0.01
- Fine-tuning time: 195.7 seconds

## Paired results
| Split | Model | Accuracy | Macro F1 | ECE | Review rate | Model latency (ms/doc) |
|---|---|---:|---:|---:|---:|---:|
| val | TF-IDF + LogReg | 0.938 | 0.936 | 0.323 | 45.8% | 0.003 |
| val | Transformer | 0.979 | 0.979 | 0.456 | 87.5% | 31.122 |
| test | TF-IDF + LogReg | 0.979 | 0.979 | 0.418 | 68.8% | 0.004 |
| test | Transformer | 0.854 | 0.858 | 0.368 | 85.4% | 33.676 |
| challenge | TF-IDF + LogReg | 0.917 | 0.916 | 0.357 | 68.8% | 0.003 |
| challenge | Transformer | 0.854 | 0.850 | 0.346 | 93.8% | 36.745 |

## Repeat-seed summary
| Seed | Val macro F1 | Test macro F1 | Challenge macro F1 | Test ECE | Test review rate |
|---:|---:|---:|---:|---:|---:|
| 7 | 0.979 | 0.858 | 0.850 | 0.368 | 85.4% |
| 42 | 0.917 | 0.873 | 0.763 | 0.533 | 100.0% |
| 73 | 0.957 | 0.936 | 0.878 | 0.386 | 72.9% |

| Split | Accuracy mean ± SD | Macro F1 mean ± SD | ECE mean ± SD | Review rate mean ± SD |
|---|---:|---:|---:|---:|
| val | 0.951 ± 0.032 | 0.951 ± 0.031 | 0.477 ± 0.086 | 86.1% ± 14.6% |
| test | 0.889 ± 0.043 | 0.889 ± 0.042 | 0.429 ± 0.090 | 86.1% ± 13.6% |
| challenge | 0.840 ± 0.043 | 0.830 ± 0.060 | 0.394 ± 0.062 | 90.3% ± 11.8% |

## Deployment cost
| Model | Artifact size | Inference device |
|---|---:|---|
| TF-IDF + LogReg | 0.09 MiB | CPU |
| Transformer | 256.12 MiB | cpu |

Latency measures model execution after vectorization/tokenization and should be compared only on the same machine. Artifact size includes tokenizer/config files for the transformer.

## Transformer training history
| Epoch | Train loss | Validation macro F1 |
|---:|---:|---:|
| 1 | 2.0505 | 0.313 |
| 2 | 1.8156 | 0.676 |
| 3 | 1.3874 | 0.916 |
| 4 | 0.9112 | 0.979 |

## Transformer test failure cases
| file | true | predicted | text snippet |
|---|---|---|---|
| documents/pa_request/doc_0038.txt | pa_request | medication_history | SOURCE DOCUMENT Regarding patient SYNTH-0039. ~ Please retain a copy of this document for... |
| documents/pa_request/doc_0058.txt | pa_request | medication_history | SOURCE DOCUMENT On file with Sample Care Network. ~ Prior Authorization Request for Drug C... |
| documents/prescription/doc_0008.txt | prescription | medication_history | SOURCE DOCUMENT Patient: SYNTH-0009. Date written: 2025-09-14. ~ Pharmacy: Example Special... |
| documents/prescription/doc_0058.txt | prescription | lab_report | SOURCE DOCUMENT Pharmacy: Example Specialty Pharmacy. NPI on file for Dr. A. Rivera. ~ Res... |
| documents/clinical_note/doc_0038.txt | clinical_note | pa_request | SOURCE DOCUMENT Case reference number: CASE-86840. ~ Please retain a copy of this document... |
| documents/medication_history/doc_0028.txt | medication_history | pa_request | SOURCE DOCUMENT On file with Sample Care Network. ~ Prior authorization on file with Demo... |
| documents/medication_history/doc_0058.txt | medication_history | pa_request | SOURCE DOCUMENT Regarding patient SYNTH-0059. ~ Drug A — 24 weeks of therapy documented. ~... |

## Transformer test classification report
```
                    precision    recall  f1-score   support

     clinical_note      1.000     0.833     0.909         6
    insurance_card      1.000     1.000     1.000         6
        lab_report      0.857     1.000     0.923         6
medication_history      0.571     0.667     0.615         6
             other      1.000     1.000     1.000         6
        pa_request      0.571     0.667     0.615         6
      prescription      1.000     0.667     0.800         6
          referral      1.000     1.000     1.000         6

          accuracy                          0.854        48
         macro avg      0.875     0.854     0.858        48
      weighted avg      0.875     0.854     0.858        48
```

## Transformer test confusion matrix (rows = true, columns = predicted)
| true \ pred | clinical_note | insurance_card | lab_report | medication_history | other | pa_request | prescription | referral |
|---|---|---|---|---|---|---|---|---|
| clinical_note | 5 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| insurance_card | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0 |
| lab_report | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 |
| medication_history | 0 | 0 | 0 | 4 | 0 | 2 | 0 | 0 |
| other | 0 | 0 | 0 | 0 | 6 | 0 | 0 | 0 |
| pa_request | 0 | 0 | 0 | 2 | 0 | 4 | 0 | 0 |
| prescription | 0 | 0 | 1 | 1 | 0 | 0 | 4 | 0 |
| referral | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 |

## Transformer challenge failure cases
| file | true | predicted | text snippet |
|---|---|---|---|
| documents/pa_request/doc_0019.txt | pa_request | insurance_card | SCANNED CORRESPONDENCE Plcase retain a copy of this document for your records. Contact the... |
| documents/pa_request/doc_0029.txt | pa_request | other | SCANNED CORRESPONDENCE Contact the office with any questions regarding this documentation.... |
| documents/pa_request/doc_0049.txt | pa_request | insurance_card | SCANNED CORRESPONDCNCC On file with Demo Health Partners. This card does not guarantee cov... |
| documents/prescription/doc_0049.txt | prescription | insurance_card | SCANNED CORRESPONDENCC Contact the office with any questions regarding this documentation.... |
| documents/medication_history/doc_0059.txt | medication_history | lab_report | SCANNED CORRESPONDENCE Med1cation h1story for SYNTH-0060. No known drug allergies rep0rted... |
| documents/lab_report/doc_0029.txt | lab_report | clinical_note | SCANNED CORRESPONDENCE Laboratory Report for SYNTH-0030. Ordering provider: Dr. K. Osei. A... |
| documents/other/doc_0009.txt | other | medication_history | SCANNED CORRESPONDENCE Office hours and hol1day closure notice. Regarding patient SYNTH-00... |

## Interpretation
Across 3 seeded runs, the transformer's mean test macro-F1 delta versus the baseline is -0.090. The selected artifact is the run with the highest validation macro F1; test and challenge results did not select it. Model choice must also account for calibration, review routing, challenge robustness, latency, artifact size, and manual failure analysis.
