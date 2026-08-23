# Phase 2 — transformer classifier experiment

Phase 2 compares a fine-tuned transformer with the Phase 1 TF-IDF + logistic-regression
baseline under the same leakage-resistant benchmark contract. The three-seed experiment is
complete: the classical baseline remains the selected classifier because it is more accurate,
more robust, smaller, faster, and easier to route at a useful confidence threshold on this corpus.

## What is implemented

`rxauth_ai.deep_classifier` provides:

- lazy loading of optional PyTorch and Hugging Face dependencies;
- deterministic seeding and explicit CPU, CUDA, or Apple MPS device selection;
- fine-tuning of a configurable pretrained sequence classifier;
- validation-only checkpoint selection and early stopping;
- typed text/path inference with the existing human-review threshold contract;
- `save_pretrained` model/tokenizer persistence plus versioned RxAuth metadata;
- val, test, and challenge accuracy, macro F1, calibration error, review rate, latency,
  confusion matrix, and failure cases;
- repeat-seed mean and sample-standard-deviation reporting;
- validation-only artifact selection across seeds and explicit hardware recording;
- artifact-size and training-time measurement;
- a paired Markdown comparison against a freshly trained classical baseline.

The evaluated deep candidate is `distilbert-base-uncased`. It remains available for learning
and future experiments, but is not the selected application classifier.

## Reproduce the comparison

From a machine with sufficient memory and network access for the initial model download:

```bash
uv sync --extra deep --group dev
uv run rxauth-build-dataset
uv run rxauth-train-deep-classifier --seeds 7 42 73
uv run pytest
```

The training command writes:

- `reports/classifier_deep_vs_baseline.md` — the tracked scientific comparison;
- `artifacts/classifier_deep/` — the local model/tokenizer bundle;
- `artifacts/classifier_baseline.pkl` — the freshly paired baseline artifact.

Artifacts stay gitignored because they are generated, large, and may contain executable
serialization in the baseline pickle. Only load artifacts created by a trusted run.

Useful experiment overrides:

```bash
uv run rxauth-train-deep-classifier \
  --model-name distilbert-base-uncased \
  --epochs 4 \
  --batch-size 16 \
  --max-length 256 \
  --device auto \
  --seeds 7 42 73
```

On PowerShell, replace the trailing backslashes with backticks or put the command on one line.

## Experimental rules

1. Keep `data/manifest.csv` and its split assignments identical for both models.
2. Fit tokenization/model parameters on train data and select the checkpoint on validation
   macro F1 only.
3. Evaluate the test split after selection. Do not tune against test results.
4. Treat the challenge split as robustness/error analysis only. Never select a model on it.
5. Use the same confidence threshold when comparing human-review routing.
6. Record the hardware because latency is machine-dependent.
7. Do not interpret synthetic-corpus performance as clinical or production validity.

## Results

The reproducible run used seeds 7, 42, and 73 on an Intel CPU. The seed-7 artifact had the
highest validation macro F1 and was saved; test and challenge results did not participate in
artifact selection.

| Split | TF-IDF + LogReg macro F1 | Transformer macro F1, mean ± SD |
|---|---:|---:|
| Validation | 0.936 | 0.951 ± 0.031 |
| Test | 0.979 | 0.889 ± 0.042 |
| Challenge | 0.916 | 0.830 ± 0.060 |

Transformer test expected calibration error was `0.429 ± 0.090`, compared with `0.418` for
the baseline. Its test human-review rate was `86.1% ± 13.6%`, compared with `68.8%`. The
selected artifact measured 256.12 MiB and 33.676 ms/document for model execution; the baseline
measured 0.09 MiB and 0.004 ms/document in the same paired invocation. See
[`reports/classifier_deep_vs_baseline.md`](../reports/classifier_deep_vs_baseline.md) for the
complete per-seed metrics, learning curve, confusion matrix, and concrete failures.

The variance is itself an important result. Seed 7 had the best validation macro F1 (`0.979`)
but only `0.858` test macro F1, while seed 73 reached `0.936` test macro F1 from a lower `0.957`
validation score. The small 48-document validation/test splits make fine-tuning and selection
unstable even though seeding is deterministic.

## Failure analysis

All seven selected-run test failures were reviewed:

- sparse PA-request documents were confused with medication history despite a short PA phrase;
- a sparse medication-history document containing “prior authorization” was confused with a
  PA request;
- prescription, clinical-note, and medication-history examples containing deliberately borrowed
  sentences were pulled toward the noise class;
- every test document uses an unseen template family, exposing layout/template sensitivity.

The selected-run challenge failures follow the same pattern with deterministic OCR-like
corruption added. Each challenge example contains heavy cross-class contamination; examples
include a PA request with insurance-card helpdesk text, a prescription with insurance emergency
language, and a lab report with clinical-note prose. The dominant failure modes are therefore
sparse class signal and cross-class noise, amplified by unseen layout and OCR corruption.

## Model decision

Keep **TF-IDF + Logistic Regression** as the application classifier. DistilBERT does not improve
held-out or challenge quality, has unstable seed-to-seed generalization, routes more documents
to review at the shared threshold, and imposes a much larger latency/artifact cost. Probability
calibration could change routing behavior but cannot repair the observed F1, robustness, and
deployment-cost gap, so calibrating a rejected candidate is not a useful next investment.

The real train/save/load/infer path was smoke-tested locally. A permanent deep-extra CI job was
not added because the deep candidate is optional and rejected; dependency-light tests continue
to cover configuration, missing-dependency behavior, sizing, and single/multi-seed report logic.

## Next step

Proceed to information extraction with confidence (README §9). Start with deterministic,
provenance-preserving extraction for the synthetic document vocabulary before introducing a
learned NLP model. Define a gold field-level evaluation set first so extraction confidence,
exact match, normalized-value accuracy, and human-review routing can be measured from the first
implementation.

## Current limitation

The corpus contains 480 synthetic text documents. It is appropriate for exercising the
engineering and evaluation workflow, but too small and artificial to justify a production model
claim. A later benchmark needs independently authored and realistically degraded synthetic or
properly governed de-identified documents before any deployment decision.
