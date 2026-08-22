# Phase 1.5 — ingestion and benchmark hardening

Phase 1.5 turns the initial text-only classifier demonstration into a defensible, reproducible benchmark boundary before introducing a deep model.

## What was added

### Typed ingestion

`rxauth_ai.ingestion.ingest_document` supports:

- UTF-8 text and Markdown;
- text-bearing PDFs through pypdf;
- PNG/JPEG/TIFF/BMP images through OpenCV preprocessing and an OCR backend.

Every result contains page number, extracted text, extraction method, and confidence. Image preprocessing applies grayscale decoding, denoising, deskewing, and Otsu thresholding. The default OCR adapter uses optional Tesseract; tests inject a deterministic backend so CI does not depend on a machine-level OCR installation.

Scanned PDFs without a text layer are rejected with an explicit instruction to rasterize pages and use the image path. The system never silently treats an empty extraction as usable evidence.

## Dataset contracts

Rebuild both corpora with:

```bash
uv run rxauth-build-dataset
```

### Classification corpus

`data/manifest.csv` contains:

```text
doc_id, filename, relative_path, label, split, char_count,
case_id, template_family_id, degradation
```

The ten template families are assigned as follows:

| Families | Split | Purpose |
|---|---|---|
| 00–06 | train | model fitting only |
| 07 | validation | threshold/model selection |
| 08 | test | primary held-out comparison |
| 09 | challenge | unseen frame, cross-class noise, OCR-like corruption |

Case IDs and template families are disjoint across splits. Loading a manifest with overlap raises an error before training.

### Ingestion corpus

`data/ingestion_manifest.csv` maps each rendered PDF/image to its synthetic ground-truth text, format, degradation, case, label, and template family. The default checked-in corpus contains 16 text-bearing PDFs and 16 images.

The scan-like images cycle through clean, rotated, blurred, low-contrast, and noisy variants as the rendered sample count grows.

## Run the benchmarks

```bash
uv run rxauth-benchmark-ingestion
uv run rxauth-train-classifier
uv run pytest
```

Outputs:

- `reports/ingestion_benchmark.md`
- `reports/classifier_baseline.md`
- `artifacts/classifier_baseline.pkl` (local, gitignored; load only trusted artifacts)

The ingestion report measures PDF character error rate and image preprocessing success. It does not publish an image OCR accuracy number without an OCR runtime. Configure the optional adapter with `uv sync --extra ocr --group dev` plus a system Tesseract installation, then run `uv run rxauth-benchmark-ingestion --run-ocr`; tests can inject another OCR backend directly.

## Classifier inference

`DocumentClassifier.predict_text` returns the predicted label, maximum class probability, and whether the confidence threshold requires human review. `classify_path` runs ingestion first and returns the typed domain `Document` plus its review flag.

The default confidence threshold is 0.65. This is a routing policy for the synthetic benchmark, not a clinically validated threshold.

## Current results

The reproducible seed-42 run contains 336 training, 48 validation, 48 test, and 48 challenge documents across eight balanced classes.

- Grouped test macro F1: 0.979
- Challenge macro F1: 0.916
- Test/challenge human-review routing rate: 68.8%
- PDF mean character error rate: 0.000
- Image preprocessing success: 100%
- Image OCR accuracy: not measured

The high routing rate and expected calibration error show that probability calibration is a more important next concern than headline accuracy alone.

## Next step

Proceed to Phase 2 with a small transformer classifier using the exact same split and reporting contracts. Compare macro F1, calibration, challenge robustness, review-routing behavior, artifact size, and latency. Do not select the model on the challenge set.
