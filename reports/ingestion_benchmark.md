# Phase 1.5 ingestion benchmark

_Reproducible: `rxauth-build-dataset` then `rxauth-benchmark-ingestion`._

## Corpus
- Rendered assets: 32
- Text-bearing PDFs: 16
- Scan-like images: 16
- Data: fully synthetic; no PHI

## Results
- PDF mean character error rate: 0.000
- Image preprocessing success rate: 100.0%
- Image OCR character error rate: not measured

Image rendering and OpenCV normalization are covered. OCR text accuracy remains explicitly unreported until an OCR runtime is configured; the benchmark accepts an injected backend and the default ingestion path supports optional Tesseract.

Character error rate is Levenshtein edit distance divided by normalized ground-truth length; lower is better.
