# Phase 2 — transformer classifier experiment

Phase 2 compares a fine-tuned transformer with the Phase 1 TF-IDF + logistic-regression
baseline under the same leakage-resistant benchmark contract. The implementation is ready;
the full benchmark and model-selection decision remain intentionally open until metrics have
been produced and reviewed.

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
- artifact-size and training-time measurement;
- a paired Markdown comparison against a freshly trained classical baseline.

The default model is `distilbert-base-uncased`. This is an experiment default, not a final
architecture decision.

## Run the first full experiment

From a machine with sufficient memory and network access for the initial model download:

```bash
uv sync --extra deep --group dev
uv run rxauth-build-dataset
uv run rxauth-train-deep-classifier
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
  --seed 42
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

## Work required to complete Phase 2

The roadmap checkbox should remain open until all of the following are done:

- Run the default seed-42 experiment and commit the generated comparison report.
- Repeat at least three seeds to estimate macro-F1 and calibration variance. The current CLI
  creates one paired run at a time; run it with distinct report destinations or extend the
  runner with aggregate multi-seed reporting.
- Review every test failure and a representative set of challenge failures. Classify errors as
  missing class signal, cross-class noise, OCR corruption, or template/layout dependence.
- Inspect the validation learning curve for underfitting/overfitting. Adjust epochs or learning
  rate using validation only if necessary, then run the held-out test once for the selected setup.
- Calibrate probabilities on validation data if the transformer still has high expected
  calibration error. Re-evaluate the review-routing threshold after calibration.
- Compare model quality with latency and artifact size. Keep the baseline if the transformer
  does not provide a defensible robustness or error-profile improvement.
- Add one load-and-infer integration test using a tiny local transformer fixture or a CI job
  with the `deep` extra; the default CI intentionally tests only dependency-free Phase 2 logic.
- Document the selected model and tradeoff decision in this guide and mark the README roadmap
  item complete only then.

## Current limitation

The corpus contains 480 synthetic text documents. It is appropriate for exercising the
engineering and evaluation workflow, but too small and artificial to justify a production model
claim. A later benchmark needs independently authored and realistically degraded synthetic or
properly governed de-identified documents before any deployment decision.
