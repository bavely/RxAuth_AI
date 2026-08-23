# RxAuth AI — `src/rxauth_ai` Code Walkthrough

A line-by-line-level reference for every module in the package: every import, every
class, every function. Companion to [milestone-0.md](milestone-0.md),
[phase-1.5.md](phase-1.5.md), [phase-2.md](phase-2.md), and
[phase-3-extraction.md](phase-3-extraction.md), which explain *why* the system is
built this way — this document explains *what each line does*.

## Module map

| Module | Role |
|---|---|
| [`models.py`](#modelspy) | Typed data model (Pydantic) shared by every other module. |
| [`ingestion.py`](#ingestionpy) | Extracts text from `.txt`/`.md`, PDF, and image files. |
| [`extraction.py`](#extractionpy) | Converts ingested text into confidence-scored evidence with exact provenance. |
| [`matching.py`](#matchingpy) | Evaluates one policy criterion against case evidence. |
| [`groundedness.py`](#groundednesspy) | Citation gate — refuses ungrounded claims. |
| [`pipeline.py`](#pipelinepy) | Wires matching + groundedness into one case report. |
| [`synthetic_case.py`](#synthetic_casepy) | Fabricated case/policy fixture for Milestone 0. |
| [`cli.py`](#clipy) | `rxauth-milestone0` — runs the fixture end to end. |
| [`rendering.py`](#renderingpy) | Renders text to deterministic PDF/PNG files. |
| [`build_dataset.py`](#build_datasetpy) | Generates the synthetic classification + ingestion corpus. |
| [`classifier.py`](#classifierpy) | TF-IDF + Logistic Regression document classifier. |
| [`train_classifier.py`](#train_classifierpy) | `rxauth-train-classifier` CLI. |
| [`deep_classifier.py`](#deep_classifierpy) | Optional PyTorch/Transformers classifier and paired comparison report. |
| [`train_deep_classifier.py`](#train_deep_classifierpy) | `rxauth-train-deep-classifier` CLI. |
| [`benchmark_extraction.py`](#benchmark_extractionpy) | Gold field/provenance/review benchmark for information extraction. |
| [`benchmark_ingestion.py`](#benchmark_ingestionpy) | `rxauth-benchmark-ingestion` CLI. |
| [`__init__.py`](#__init__py) | Package marker + version. |

Console scripts (from `pyproject.toml`), left to right in rough pipeline order:

| Command | Entry point |
|---|---|
| `rxauth-build-dataset` | `build_dataset:main` |
| `rxauth-ingest` | `ingestion:main` |
| `rxauth-extract` | `extraction:main` |
| `rxauth-train-classifier` | `train_classifier:main` |
| `rxauth-train-deep-classifier` | `train_deep_classifier:main` |
| `rxauth-benchmark-extraction` | `benchmark_extraction:main` |
| `rxauth-benchmark-ingestion` | `benchmark_ingestion:main` |
| `rxauth-milestone0` | `cli:main` |

Dependency direction (who imports whom): `models` is imported by almost everything;
`ingestion` is imported by both classifiers, `extraction`, and `benchmark_ingestion`; `matching` and
`groundedness` are imported only by `pipeline`; `pipeline` and `synthetic_case` are
imported only by `cli`; `build_dataset` imports `rendering` lazily; `train_classifier`
imports `classifier`; `deep_classifier` reuses the classical classifier's dataset,
prediction, and calibration contracts; `train_deep_classifier` orchestrates both
classifiers so the generated comparison comes from the same loaded splits;
`benchmark_extraction` imports the extractor plus ingestion/evidence contracts and evaluates
hand-authored JSONL without changing the extractor.

Every CLI-capable module ends with the same guard:

```python
if __name__ == "__main__":
    main()
```

This lets a module also be run directly (`python -m rxauth_ai.ingestion ...`), not just
through its installed console-script wrapper.

---

## `models.py`

The Pydantic data model everything else is built on. Its docstring states the design
rule this whole codebase follows: every value an AI component produces carries
**provenance** (source document, page, span, confidence, method), so no normalized
value ever exists without a trace back to where it came from.

### Imports

| Import | Why |
|---|---|
| `from __future__ import annotations` | Postpones evaluation of type hints so forward references (a class referring to another defined later, or to itself) don't need string quoting. |
| `from enum import Enum` | Base class for the three closed-vocabulary enums below. |
| `from typing import Literal, Optional` | `Literal` restricts a field to an exact set of string values; `Optional` marks nullable fields. |
| `from pydantic import BaseModel, Field, model_validator` | `BaseModel` gives every class validation, JSON (de)serialization, and `.model_dump()`; `Field` adds per-field constraints/metadata; `model_validator` enforces relationships between provenance offsets. |

### Enums

- **`DocumentType(str, Enum)`** — the 8-way document taxonomy the classifier predicts
  and the pipeline consumes: `PA_REQUEST`, `INSURANCE_CARD`, `REFERRAL`,
  `PRESCRIPTION`, `CLINICAL_NOTE`, `MEDICATION_HISTORY`, `LAB_REPORT`, `OTHER`.
  Inheriting from `str` means members compare equal to their plain string value
  (`DocumentType.PA_REQUEST == "pa_request"`), which is what lets the classifier's raw
  string predictions round-trip into this enum via `DocumentType(prediction.label)`.
- **`CriterionResult(str, Enum)`** — the five-state outcome of evaluating one
  criterion: `SATISFIED`, `NOT_SATISFIED`, `MISSING`, `AMBIGUOUS`,
  `HUMAN_REVIEW_REQUIRED`.
- **`EvaluationMethod(str, Enum)`** — how a `CriterionEvaluation` was produced:
  `DETERMINISTIC` (plain Python rule), `MODEL_ASSISTED` (marks where an LLM would take
  over in a later phase — no model is actually called in Milestone 0), or `NONE` (no
  evaluation attempted, e.g. routed straight to a human).

### Models

- **`Provenance`** — `document_id`, `filename`, `page`, page-relative `start_char`
  (inclusive), `end_char` (exclusive), and `source_text`, all optional. Character offsets
  are non-negative, must be supplied together, and cannot be reversed; the
  `validate_character_span` model validator enforces that contract.
  Attached to every extracted value and every criterion so a reviewer can always see
  exactly where a fact came from.
- **`Document`** — one classified file: `id`, `filename`, `document_type`,
  `classification_confidence` (constrained to `[0.0, 1.0]` via `Field(ge=..., le=...)`),
  `page_count` (default `1`).
- **`Evidence`** — one normalized fact pulled from a document: `id`, `evidence_type`
  (free-form string matched against `Criterion.criterion_type`), optional
  `medication`/`text_value`/numeric `value`/`unit`/`outcome`, `confidence` (`[0,1]`), a required
  `provenance`, and `extraction_method` (default `"synthetic"` since Milestone 0
  fabricates evidence rather than extracting it).
- **`Criterion`** — one structured payer requirement: `id`, `policy_id`,
  `description`, `criterion_type`, optional `medication`, an optional `operator`
  restricted by `Literal[">=", "<=", ">", "<", "==", "exists"]`, optional
  `expected_value`/`unit`/`required_outcome`, and a required `provenance` (page +
  quoted policy text).
- **`Policy`** — `id`, `payer`, `medication`, `indication`, `effective_date`,
  optional `source_url`, `version` (default `"v1"`), and `criteria: list[Criterion]`
  (default empty via `Field(default_factory=list)` — never a mutable-default-argument
  bug).
- **`Case`** — `id`, `patient_synthetic_id`, `payer`, optional `plan`, `medication`,
  `indication`, `pa_required` (its `description=` documents that this must always come
  from a synthetic trigger or user input — **never inferred from policy text**),
  `documents: list[Document]`, `evidence: list[Evidence]`.
- **`CriterionEvaluation`** — the result of checking one criterion: `criterion_id`,
  `case_id`, `result`, `supporting_evidence_ids` (list, default empty),
  `confidence`, `evaluation_method`, `explanation` (human-readable reason), plus
  denormalized `criterion_description`, and the two provenance pointers
  `policy_source` / `patient_evidence_source` that the groundedness gate checks.
- **`CaseReadinessReport`** — the full Milestone-0 output for one case: identity
  fields (`case_id`, `policy_id`, `payer`, `medication`, `indication`,
  `pa_required`), `documents_detected`, `mean_classification_confidence`, the four
  criteria tallies (`criteria_total`/`_satisfied`/`_not_satisfied`/`_missing`/
  `_needs_review`), `groundedness_gate` (`"PASS"`/`"FAIL"` string), and the full
  `evaluations` list for audit.
  - **`summary_line(self) -> str`** — formats
    `"{satisfied} supported, {needs_review} need review, {missing} missing (of {total})"`
    — the one-line readiness verdict printed by the CLI.

---

## `ingestion.py`

The ingestion boundary: turns a file on disk into page-level text plus the method and
confidence that produced it. Plain text and text-bearing PDFs need no OCR engine;
images are normalized with OpenCV and handed to a pluggable OCR backend.

### Imports

| Import | Why |
|---|---|
| `argparse` | Powers the `rxauth-ingest` CLI. |
| `json` | Pretty-prints the CLI's output. |
| `from collections.abc import Callable` | Types the `OCRBackend` alias. |
| `from pathlib import Path` | All file paths are `Path` objects. |
| `from typing import Any, Literal` | `Any` for opaque third-party objects (decoded images); `Literal` for closed string fields. |
| `from pydantic import BaseModel, Field` | Same role as in `models.py`. |
| `cv2`, `numpy`, `pytesseract`, `pypdf` | **Imported lazily inside functions**, not at module top — so importing `rxauth_ai.ingestion` doesn't force every optional dependency to be installed just to, say, ingest a `.txt` file. |

### Exceptions

- **`IngestionError(RuntimeError)`** — base error for anything that stops ingestion.
- **`OCRUnavailableError(IngestionError)`** — specifically, no working OCR runtime.

### Models

- **`IngestedPage`** — `page_number` (`ge=1`), `text`, `extraction_method`
  (`Literal["text", "pypdf", "ocr"]`), `confidence` (`[0,1]`).
- **`IngestedDocument`** — `filename`, `media_type` (`Literal["text","pdf","image"]`),
  `pages: list[IngestedPage]`, `preprocessing: list[str]` (steps applied, e.g. for
  images).
  - **`text` (property)** — joins every page's stripped text with a blank line,
    skipping pages that are empty after stripping.
  - **`mean_confidence` (property)** — average of all page confidences; `0.0` if
    there are no pages (avoids a `ZeroDivisionError`).

### Type alias

`OCRBackend = Callable[[Any], tuple[str, float]]` — the contract any OCR backend must
satisfy: take a preprocessed image, return `(text, confidence)`. This is what lets
`benchmark_ingestion.py` inject a fake/deterministic backend in tests instead of
depending on a real Tesseract install.

### Module constants

`_TEXT_SUFFIXES = {".txt", ".md"}` and `_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg",
".tif", ".tiff", ".bmp"}` — used to dispatch `ingest_document` by file extension.

### Functions

- **`_deskew(image, cv2, np) -> Any`** — straightens a scanned page.
  1. `np.where(image < 250)` finds the pixel coordinates of everything darker than
     near-white (i.e. ink), stacked into `(x, y)` pairs.
  2. If fewer than 20 such pixels exist (a nearly blank page), returns the image
     unchanged — not enough signal to compute a rotation.
  3. `cv2.minAreaRect` fits the smallest rotated bounding box around those points and
     returns its angle; the following two lines correct for OpenCV's angle
     convention quirk (angles are reported in `[-90, 0)`, so an angle under -45° is
     really rotated the other way).
  4. If the corrected angle is under `0.05°`, skips rotation (not worth the
     interpolation cost/quality loss).
  5. Otherwise builds a rotation matrix about the image center and warps the image
     with cubic interpolation, filling any newly-exposed corners with white
     (`borderValue=255`).
- **`preprocess_image(path) -> Any`** — the full image-normalization pipeline used
  before OCR (and exercised standalone by the ingestion benchmark even when OCR isn't
  run).
  1. Lazily imports `cv2`/`numpy`; if missing, raises `IngestionError` telling the
     user to install the project's (optional) ingestion dependencies.
  2. Reads the file via `np.fromfile` and decodes it grayscale with `cv2.imdecode`
     (reading bytes first, rather than `cv2.imread(path)`, avoids a known OpenCV
     issue with non-ASCII/Windows paths); raises `IngestionError` if decoding
     produced `None` (corrupt file or wrong format).
  3. Denoises with `cv2.fastNlMeansDenoising`.
  4. Deskews via `_deskew`.
  5. Binarizes with Otsu's automatic thresholding
     (`cv2.THRESH_BINARY + cv2.THRESH_OTSU`) and returns the thresholded array.
- **`_tesseract_backend(image) -> tuple[str, float]`** — the default `OCRBackend`.
  1. Imports `pytesseract` via `importlib.import_module` (lazy, so the module can be
     imported without the optional `ocr` extra installed) and grabs its `Output`
     enum; raises `OCRUnavailableError` with install instructions if unavailable.
  2. Calls `pytesseract.image_to_data(image, output_type=Output.DICT)` to get
     word-level text + per-word confidence; wraps any failure (e.g. the Tesseract
     system binary itself is missing) into `OCRUnavailableError`.
  3. Iterates `zip(data["text"], data["conf"], strict=True)` (the `strict=True`
     guards against a backend that returns mismatched-length arrays — a contract
     violation would raise loudly instead of silently misaligning words and
     confidences). Skips blank words. Converts each confidence string to a `0-1`
     float, skipping unparsable or negative values (Tesseract emits `-1` for
     non-text regions).
  4. Joins the surviving words with spaces and averages the surviving confidences
     (`0.0` if none survived).
- **`ingest_document(path, *, ocr_backend=None) -> IngestedDocument`** — the main
  entry point, dispatching purely on file extension:
  - **Text (`.txt`/`.md`)** — reads the whole file as UTF-8 and returns one page with
    `extraction_method="text"` and `confidence=1.0` (nothing to guess at).
  - **PDF** — lazily imports `pypdf.PdfReader` (raises `IngestionError` if missing);
    for each page (`enumerate(..., start=1)`), extracts text (`or ""` if
    `extract_text()` returns `None`), and sets `confidence=1.0` if the page has any
    non-whitespace text, else `0.0`. If **no** page produced text at all, raises
    `IngestionError` telling the caller this is a scanned/image-only PDF that needs
    rasterizing + OCR instead.
  - **Image** — runs `preprocess_image`, then calls the injected `ocr_backend` or
    falls back to `_tesseract_backend`, wrapping the result into one page tagged
    `extraction_method="ocr"` with `preprocessing=["grayscale", "denoise", "deskew",
    "otsu_threshold"]` recorded for auditability.
  - **Anything else** — raises `IngestionError` naming the unsupported extension.
- **`main()`** — the `rxauth-ingest` CLI: one positional `path` argument, ingests it,
  and prints `ingest_document(path).model_dump()` as indented JSON.

---

## `extraction.py`

The first README §9 slice (see [phase-3-extraction.md](phase-3-extraction.md)):
deterministic, pattern-based extraction of normalized `Evidence` from the shared
`IngestedDocument` boundary. Its purpose is to establish the provenance and
review-routing contract — every value traceable to an exact source span, every
low-confidence value flagged rather than silently trusted or discarded — before a
learned NLP model is ever introduced.

### Imports

| Import | Why |
|---|---|
| `argparse`, `json` | Parse the `rxauth-extract` command and print its result as indented JSON. |
| `re` | Compile and run the sixteen deterministic extraction patterns. |
| `dataclass` | Defines the internal `_ExtractedFields`/`_ExtractionRule` records and the `ExtractionResult` return type. |
| `Path` | Types the CLI's input path. |
| `Callable`, `Optional` | Type each rule's field-builder function and its optional output fields. |
| `BaseModel`, `Field` | Gives `ExtractionIssue` validation, bounded confidence, and `.model_dump()` for the CLI's JSON output. |
| `IngestedDocument`, `ingest_document` | Reuses the Phase 1.5 ingestion boundary rather than re-implementing text extraction. |
| `Evidence`, `Provenance` | Emits the same typed domain entities `matching.py` and `groundedness.py` already consume. |

### Module constants

- **`EXTRACTOR_VERSION = "regex-v1"`** — recorded on every `Evidence.extraction_method`
  it produces, so a later learned extractor can be versioned and compared instead of
  silently replacing this baseline.
- **`DEFAULT_CONFIDENCE_THRESHOLD = 0.65`** — the same review-routing default already
  used by `DocumentClassifier`/`DeepDocumentClassifier`.

### `ExtractionIssue(BaseModel)`

One flag on a retained-but-uncertain `Evidence` item: `evidence_id`, `evidence_type`,
bounded `confidence`, and a human-readable `reason`. Raising an issue never causes the
evidence itself to be dropped — it's an additional signal alongside it.

### `ExtractionResult` (`@dataclass`)

The return value of `extract_evidence`: `evidence: list[Evidence]` and
`issues: list[ExtractionIssue]`.

- **`requires_human_review` (property)** — `bool(self.issues)`. This is the flag the
  CLI surfaces at the top level of its JSON output.

### `_ExtractedFields` (`@dataclass(frozen=True)`)

The internal, pre-`Evidence` bundle every rule's builder function returns:
`evidence_type`, and optional `medication`/`text_value`/`value`/`unit`/`outcome`, plus
`confidence` (default `0.9`). Keeps each builder focused on producing *values*;
`extract_evidence` alone is responsible for turning that into a fully-provenanced
`Evidence` record.

### Normalization helpers

- **`_normalize_duration_unit(raw) -> str`** — lowercases and folds any singular/plural
  `week(s)`/`month(s)`/`day(s)` capture to the canonical `weeks`/`months`/`days`, so
  `matching.py`'s unit-equality check (`criterion.unit.casefold() !=
  evidence.unit.casefold()`) doesn't fail on a plural mismatch.
- **`_normalize_outcome(raw) -> Optional[str]`** — lowercases and replaces spaces with
  underscores (`"inadequate response"` → `"inadequate_response"`), matching the
  underscore convention every `Criterion.required_outcome` in `synthetic_case.py`
  already uses. Returns `None` unchanged.

### Field-builder functions

Each takes a `re.Match` and returns `_ExtractedFields`. One per recognized form:

| Builder | Recognizes | Confidence |
|---|---|---:|
| `_diagnosis_fields` | Confirmed `Diagnosis:`/`Assessment:`; a fixed-width negative lookbehind excludes `No Diagnosis:` | `0.95` |
| `_prescription_fields` | `Rx: Drug X.` | `0.9` |
| `_previous_therapy_used_for_fields` | `Drug X used for N weeks[; OUTCOME]` (the Milestone-0 fixture's own phrasing) | `0.9` with outcome, else `0.85` |
| `_previous_therapy_documented_fields` | `Drug X — N weeks of therapy documented.` (the real corpus's phrasing — this form never carries an outcome) | `0.85` |
| `_previous_therapy_outcome_only_fields` | `Drug X — started DATE, discontinued due to OUTCOME` (outcome present, no duration) | `0.60` |
| `_lab_value_fields` | `A1c: N[%]` | `0.95` with an explicit `%`, else `0.75` (the real corpus never includes `%`) |
| `_patient_id_fields` | `Patient ID: IDENTIFIER` | `0.98` |
| `_member_id_fields` | `Member ID: IDENTIFIER` | `0.98` |
| `_payer_fields` | `Health plan: NAME` or `NAME Health Plan Member Identification Card` | `0.95` |
| `_days_supply_fields` | `Quantity requested: N-day supply` | `0.95` |
| `_prescription_quantity_fields` | `Quantity: N` | `0.95` |
| `_document_date_fields` | `Date written:`, `Date of request:`, or `Visit date:` followed by an ISO date | `0.98` |
| `_additional_lab_fields` | Numeric LDL cholesterol, ALT, eGFR, or CRP; maps the label to a typed `lab_*` evidence type | `0.9` |
| `_screening_documentation_fields` | Exact `Screening documentation attached` statement | `0.9` |
| `_therapy_duration_vague_fields` | `on therapy for {several months / a few weeks / some time / an extended period}` | `0.60` |

The two `0.60` confidences are a deliberate seam, not a rounding coincidence: `0.60`
clears `matching.py`'s own `evidence.confidence < 0.60` human-review gate (so a
numeric-comparison criterion correctly falls through to `AMBIGUOUS` instead of
`HUMAN_REVIEW_REQUIRED` — see the `matching.py` section above) while still sitting
below `DEFAULT_CONFIDENCE_THRESHOLD` (`0.65`), so `extract_evidence` still raises an
`ExtractionIssue` for it. This module's issue list and `matching.py`'s five-state
result end up agreeing the value needs a human, for consistent reasons.

### `_ExtractionRule` (`@dataclass(frozen=True)`) and `_RULES`

`_ExtractionRule` pairs one compiled, case-insensitive `re.Pattern` with its builder
function. `_RULES` is the ordered list of all sixteen: patient/member IDs; two payer
forms; days supply; prescription quantity; document dates; added labs; screening
documentation; diagnosis; prescription; three previous-therapy forms; A1c; and vague duration.
Order determines the sequence (not the correctness) of the returned evidence list; one
page can and does match more than one rule.

### `extract_evidence(document, *, document_id, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD) -> ExtractionResult`

1. Walks `document.pages` in order; for each page, runs every rule's pattern via
   `.finditer(page.text)` so multiple matches of the same rule on one page are all
   captured.
2. For each match, calls the rule's builder to get `_ExtractedFields`, then builds a
   full `Evidence`: a deterministic ID (`f"{document_id}-EV{n}"`, `n` counting up
   across the whole document, not per-page or per-rule), the builder's fields, and a
   `Provenance` carrying `document_id`, `filename`, `page.page_number`,
   `match.start()`/`match.end()` (inclusive/exclusive, matching Python slicing and
   `Provenance`'s own validator), and `match.group(0)` as `source_text`.
3. Appends the `Evidence` to the result unconditionally — nothing is ever dropped for
   low confidence.
4. If `fields.confidence < confidence_threshold`, additionally appends an
   `ExtractionIssue` referencing that evidence's ID.
5. Returns `ExtractionResult(evidence, issues)`.

A page with no matching text simply contributes no evidence for that type — an absent
requirement stays `MISSING` when `matching.py` later evaluates it, rather than being
invented.

### `main()`

The `rxauth-extract` CLI: one positional `path`, a required `--document-id`, and
`--confidence-threshold` (default `DEFAULT_CONFIDENCE_THRESHOLD`, validated into
`[0, 1]` via `parser.error(...)`). Calls `ingest_document(path)`, then
`extract_evidence`, then prints indented JSON with three top-level keys: `evidence`
(each item's `.model_dump()`), `issues` (same), and `requires_human_review` (the
`ExtractionResult` property) — exactly the shape
[phase-3-extraction.md](phase-3-extraction.md) documents.

The current rules are an engineering baseline, not measured probabilities. The Phase 3
gold JSONL benchmark now protects this contract. Broader medication names, multi-span
provenance, overlap/deduplication, and calibrated confidence are still required before
connecting extraction to the case pipeline.

### Phase 3 tests

`tests/test_extraction.py` (20 tests) covers every rule plus integration checks
that exercise the module against real inputs, not just crafted strings:

- one test per recognized form (`Diagnosis:`, the real corpus's `Assessment:` form,
  `Rx:`, both previous-therapy duration phrasings, the outcome-only phrasing, A1c with
  and without `%`, and the vague-duration phrasing), each checking the specific typed
  fields it should populate;
- grouped tests cover patient/member/payer fields, the payer-card heading, quantities and
  all three date labels, all four added labs, and screening-document presence;
- the two intentionally-low-confidence forms (outcome-only previous-therapy, vague
  duration) assert `confidence < DEFAULT_CONFIDENCE_THRESHOLD` *and* that exactly one
  `ExtractionIssue` is raised;
- `test_confidence_threshold_is_configurable` passes `confidence_threshold=0.0` and
  checks no issue is raised even for an otherwise-flagged match;
- provenance tests confirm `text[start:end] == source_text` (the offset contract
  actually round-trips) and that a multi-page document assigns each match to its own
  page number;
- `test_evidence_ids_are_unique_and_ordered` guards the ID-generation scheme;
- the negated-diagnosis regression test proves `No Diagnosis:` cannot create supported
  diagnosis evidence;
- `test_extracted_evidence_reproduces_milestone_zero_criterion_outcomes` runs the real
  extractor over document text and feeds the result into `matching.evaluate_case`,
  asserting all six Milestone-0 criteria land on the exact same result (`SATISFIED`
  ×4, `MISSING`, `AMBIGUOUS`) as the hand-authored `synthetic_case.py` fixture —
  proving extraction is a drop-in replacement for pre-supplied evidence;
- `test_extracts_evidence_from_the_documented_cli_example_file` runs extraction
  against the exact corpus file (`data/documents/clinical_note/doc_0002.txt`) that
  this file's and the README's `rxauth-extract` usage example references, so the
  documented command is guaranteed to actually produce evidence.

---

## `matching.py`

The core intelligence layer: evaluates a single policy criterion against a case's
evidence and returns one of the five `CriterionResult` states. Its docstring states
the guiding principle — evaluate deterministically wherever a rule is explicit, and
route genuinely ambiguous evidence to a human instead of guessing; it *marks* where a
future LLM would take over without ever calling one.

### Imports

`from __future__ import annotations`; `from typing import Optional`; and from
`.models`: `Case`, `Criterion`, `CriterionEvaluation`, `CriterionResult`,
`EvaluationMethod`, `Evidence`.

### Module constant

`_OPERATORS` — a dict mapping `">="`, `"<="`, `">"`, `"<"`, `"=="` to the equivalent
Python lambda (`lambda a, b: a >= b`, etc.). This is the whitelist of comparisons the
matcher can evaluate without any model.

### Functions

- **`_find_evidence(criterion, case) -> Optional[Evidence]`** — picks the best
  evidence item for one criterion.
  1. Filters `case.evidence` to items whose `evidence_type` matches the criterion's
     `criterion_type`.
  2. If the criterion names a `medication`, further filters to evidence naming the
     *same* medication (case-insensitive, via `.casefold()`).
  3. Returns `None` if nothing survives.
  4. Otherwise returns the highest-confidence remaining candidate
     (`max(candidates, key=lambda e: e.confidence)`) — Milestone 0's stand-in for a
     future retrieval + model-assisted linking step.
- **`evaluate_criterion(criterion, case) -> CriterionEvaluation`** — the five-state
  decision tree. Every branch shares a `base` dict (`criterion_id`, `case_id`,
  `criterion_description`, `policy_source=criterion.provenance`) spread in via
  `**base`; once evidence is found, `patient_evidence_source` is added to `base` too,
  so it's automatically attached to every branch below it.
  1. **No matching evidence at all** → `MISSING`, `confidence=0.99`,
     `DETERMINISTIC` — we're highly confident the evidence genuinely isn't there.
  2. **Evidence found but its own `confidence < 0.60`** → `HUMAN_REVIEW_REQUIRED`,
     method `NONE` — the underlying extraction is too unreliable to reason about at
     all, so no comparison is even attempted.
  3. **Criterion needs a numeric comparison** (`operator` is one of `_OPERATORS` and
     `expected_value` is set):
     - If `evidence.value` is `None` (present but not a parseable number, e.g. "several
       months") → `AMBIGUOUS`, method `MODEL_ASSISTED` — explicitly marked as the seam
       where a future LLM would attempt interpretation; here it's simply routed to a
       human.
     - If both `criterion.unit` and `evidence.unit` are set but differ →
       `HUMAN_REVIEW_REQUIRED` — refuses to silently assume a unit conversion,
       confidence capped at `0.50`.
     - Otherwise runs the actual comparison: `_OPERATORS[criterion.operator](evidence.value,
       criterion.expected_value)`. If the criterion also requires a specific
       `required_outcome` (e.g. `"inadequate_response"`), that's checked too
       (case-insensitive string equality against `evidence.outcome`). Result is
       `SATISFIED` only if **both** the numeric comparison and the outcome check pass,
       else `NOT_SATISFIED`. Confidence is `min(evidence.confidence, 0.98)` — the
       evaluation is never more confident than the evidence it rests on, and never
       claims absolute (`1.0`) certainty.
  4. **Outcome-only criterion** (no numeric operator, but `required_outcome` is set)
     and evidence exists → compares `outcome` strings case-insensitively, returns
     `SATISFIED`/`NOT_SATISFIED` accordingly.
  5. **Existence-only criterion** (no operator, no `required_outcome`) — evidence being
     present at all is sufficient → `SATISFIED`.
- **`evaluate_case(case, criteria) -> list[CriterionEvaluation]`** — one line:
  `[evaluate_criterion(c, case) for c in criteria]`. Produces the full per-criterion
  audit trail for a case.

---

## `groundedness.py`

The citation gate (main README §14). In Milestone 0 there's no generated prose to
fact-check, so it enforces the *structural* version of groundedness: every concrete
claim must cite where it came from. The docstring notes that a later phase adds a
semantic faithfulness check (e.g. Ragas) behind the same PASS/FAIL interface.

### Imports

`from __future__ import annotations`; `from dataclasses import dataclass, field`; and
from `.models`: `CriterionEvaluation`, `CriterionResult`.

### `GroundednessResult` (`@dataclass`)

- `passed: bool`
- `issues: list[str] = field(default_factory=list)` — every failure reason found.
- **`status` (property)** — `"PASS"` if `passed` else `"FAIL"`.

### Module constant

`_NEEDS_PATIENT_EVIDENCE = {CriterionResult.SATISFIED, CriterionResult.NOT_SATISFIED}`
— the two results that assert something concrete about the patient, and therefore
must be backed by cited evidence.

### `check_groundedness(evaluations) -> GroundednessResult`

Iterates every evaluation and appends a human-readable issue string for any of three
violations:

1. `supporting_evidence_ids` is non-empty but `patient_evidence_source` is `None` —
   evidence is cited by ID with no provenance record (an internal contradiction/bug
   flag).
2. `result` is `SATISFIED` or `NOT_SATISFIED` but `supporting_evidence_ids` is empty —
   a concrete claim with nothing behind it.
3. `policy_source` is `None` — every evaluation must know which policy requirement it
   answers.

Returns `GroundednessResult(passed=not issues, issues=issues)`.

---

## `pipeline.py`

Wires classification (pre-supplied in Milestone 0), matching, and the groundedness
gate into one `CaseReadinessReport`. The docstring is explicit that what this proves
is the *spine*: structured entities flowing through a deterministic evaluation core,
every result carrying provenance, uncertainty routed to a human, and a groundedness
gate before anything is shown.

### Imports

`from .groundedness import check_groundedness`; `from .matching import evaluate_case`;
and from `.models`: `Case`, `CaseReadinessReport`, `CriterionResult`, `Policy`.

### `run_pipeline(case, policy) -> CaseReadinessReport`

1. **Consistency check.** Compares `payer`, `medication`, `indication` on `case` vs.
   `policy` case-insensitively; collects any field names that don't match and raises
   `ValueError` listing them — guards against silently evaluating a case against the
   wrong policy.
2. **Evaluate.** `evaluate_case(case, policy.criteria)` runs every criterion.
3. **Gate.** `check_groundedness(evaluations)` — nothing is "ready" if a claim lacks a
   source.
4. **Tally.** Builds `counts = {r: 0 for r in CriterionResult}` (every enum member
   pre-seeded at zero) then increments per evaluation.
5. `needs_review = counts[AMBIGUOUS] + counts[HUMAN_REVIEW_REQUIRED]` — both are
   "send to a human" outcomes, just for different reasons.
6. `mean_conf` — average `classification_confidence` across `case.documents`, or
   `0.0` if there are none.
7. Returns a `CaseReadinessReport` bundling the case/policy identity fields, document
   count, mean confidence (rounded to 3 decimals), every criteria tally, the
   groundedness gate's `.status` string, and the full `evaluations` list.

---

## `synthetic_case.py`

The fabricated case + policy fixture used by Milestone 0. Its module docstring
repeats the project guardrail: every patient, document, and value here is fabricated
placeholder data — no real PHI, payer, or drug.

### Imports

From `.models`: `Case`, `Criterion`, `Document`, `DocumentType`, `Evidence`, `Policy`,
`Provenance`.

### `POLICY_ID = "PA-104"`

### `build_policy() -> Policy`

Builds six `Criterion` fixtures, each carrying a `Provenance` with a fake page number
and a quoted snippet of fictional policy text:

| ID | Type | Rule | Purpose in the fixture |
|---|---|---|---|
| C1 | `diagnosis` | exists | drives a `SATISFIED` via existence-only. |
| C2 | `previous_therapy` | Drug A `>= 12` weeks | drives a `SATISFIED` numeric comparison. |
| C3 | `previous_therapy` | Drug A `>= 12` weeks **and** outcome `inadequate_response` | drives a `SATISFIED` numeric + outcome comparison. |
| C4 | `lab_a1c` | `< 8.0` percent | drives a `SATISFIED` numeric comparison. |
| C5 | `screening_doc` | exists | left with **no** matching evidence on purpose → `MISSING`. |
| C6 | `therapy_duration` | `>= 8` weeks | matching evidence has no numeric value → `AMBIGUOUS`. |

Returns a `Policy` with `id=POLICY_ID`, `payer="Example Health Plan"`,
`medication="Drug A"`, `indication="Example Condition"`, a fake `effective_date` and
`source_url`, and the six criteria.

### `build_case() -> Case`

Builds five `Document` fixtures (`pa_request.pdf`, `insurance_card.png`,
`clinical_note.pdf`, `medication_history.pdf`, `lab_report.pdf`), each with a
`document_type` and a plausible `classification_confidence` (0.94–0.98).

Builds four `Evidence` fixtures, each with a `Provenance` pointing at one of those
documents:

- **E1** — `diagnosis`, outcome `"documented"` → satisfies C1.
- **E2** — `previous_therapy`, Drug A, `value=16` weeks, outcome
  `"inadequate_response"` → satisfies both C2 and C3 (16 ≥ 12, and the outcome
  matches).
- **E3** — `lab_a1c`, `value=7.4` percent → satisfies C4 (7.4 < 8.0).
- **E4** — `therapy_duration`, `value=None`, outcome `"several months"` (not a
  parseable number) → this is what makes C6 come out `AMBIGUOUS`.
- (No evidence has `evidence_type="screening_doc"` — that absence is what makes C5
  come out `MISSING`.)

Returns a `Case` with `id="PA-DEMO-001"`, a synthetic patient ID, payer/medication/
indication matching the policy exactly, `pa_required=True` (the comment reiterates
this is a synthetic trigger flag, never inferred from policy text), and the
documents/evidence lists above.

---

## `cli.py`

The `rxauth-milestone0` entry point: runs the one fixture case end to end
(Python-only, no DB, no LLM, no network) and prints a human-readable report plus a
structured JSON file.

### Imports

`from __future__ import annotations`; `argparse`; `from pathlib import Path`; from
`.models`: `CriterionResult`; `from .pipeline import run_pipeline`; from
`.synthetic_case`: `build_case`, `build_policy`.

### `_ICON`

A dict mapping each `CriterionResult` to a short bracketed glyph for terminal output:
`[ok ]`, `[no ]`, `[gap]`, `[?? ]`, `[hum]` for `SATISFIED`, `NOT_SATISFIED`,
`MISSING`, `AMBIGUOUS`, `HUMAN_REVIEW_REQUIRED` respectively.

### `print_report(report, evaluations) -> None`

Prints the intake summary block (case ID, payer/policy, medication/indication,
`pa_required`, document count, mean confidence as a percentage, all four criteria
tallies, the groundedness gate status, and `report.summary_line()`), then iterates
`evaluations` printing, per criterion: the icon + ID + description, the result value
+ evaluation method + confidence, the `explanation` text, and — if present — the
patient evidence's filename/page/quoted `source_text`, and the policy source's page.

### `main()`

1. Parses `--json-only` (print JSON only) and `--output-dir` (default `./reports`).
2. Builds the fixture `case` and `policy`, runs `run_pipeline`.
3. Ensures the output directory exists and writes
   `<output-dir>/case_<case_id>.json` via `report.model_dump_json(indent=2)`.
4. If `--json-only`, prints the JSON and returns immediately.
5. Otherwise calls `print_report`, then prints the output path — relative to the
   current working directory when possible (`Path.is_relative_to`), else absolute.

---

## `rendering.py`

Deterministic synthetic PDF/image rendering, used only to build the ingestion
benchmark's rendered corpus.

### Imports

`from __future__ import annotations`; `random`; `textwrap`; `from pathlib import
Path`; `from typing import Literal`. `reportlab`, `cv2`, `numpy`, and `PIL` are
imported lazily inside each function, keeping these render-only dependencies out of
the module's top-level import.

### `Degradation`

`Literal["clean", "rotated", "blurred", "low_contrast", "noisy"]` — the five scan
artifacts this module can simulate.

### `render_text_pdf(text, path) -> None`

1. Creates a `reportlab` `Canvas` with `invariant=1` (no embedded timestamps — the
   file is byte-identical across runs) and `pageCompression=0` (uncompressed
   streams, also for reproducibility).
2. Manually paginates: for each paragraph (`text.splitlines()`), wraps it to 92
   characters (`textwrap.wrap`) and writes each line; whenever the text cursor drops
   below the bottom margin (`54pt`), flushes the current page (`drawText` +
   `showPage`) and starts a fresh text object.
3. Draws the final page and saves.

### `render_text_image(text, path, *, degradation="clean", seed=42) -> None`

1. Wraps `text` into 76-character lines and creates a grayscale (`"L"` mode) PIL
   image sized to fit them (minimum height 420px).
2. Draws each line in dark gray (`fill=20`) with PIL's default font.
3. Applies exactly one degradation:
   - `"rotated"` — picks one of four small angles deterministically from
     `random.Random(seed)`, rotates with bicubic resampling, fills exposed corners
     white.
   - `"blurred"` — Gaussian blur, radius `1.2`.
   - `"low_contrast"` — contrast reduced to 42% via `ImageEnhance.Contrast`.
   - `"noisy"` — adds `Normal(0, 18)` noise via a seeded `numpy` generator, clipped
     to the valid `0–255` pixel range.
   - `"clean"` — no-op.
   - anything else — raises `ValueError`.
4. Encodes as PNG (`cv2.imencode`, max compression level 9) and writes the bytes
   directly with `path.write_bytes(...)` (bypassing OpenCV's own file writer, the
   same non-ASCII-path-safety pattern used in `ingestion.py`).

---

## `build_dataset.py`

The synthetic dataset builder (main README §7): generates fabricated,
template-based documents across all 8 `DocumentType` classes, split by
**template family** and **case** so the classifier's train/val/test/challenge splits
never leak a layout or a patient case across boundaries. Deterministic: the same
`--seed`/`--per-class` always produce byte-identical output.

### Imports

`from __future__ import annotations`; `argparse`; `csv`; `random`; `re`; `from
pathlib import Path`; `from .models import DocumentType`. (`rendering` functions are
imported lazily, inside the loop, only when `--rendered-per-class > 0`.)

### Fabricated vocabularies (README §3 guardrail: all placeholders)

- `SYNTH_PATIENTS` — 299 fake IDs, `"SYNTH-0001"`…`"SYNTH-0299"`.
- `SYNTH_PROVIDERS`, `SYNTH_PAYERS`, `SYNTH_DRUGS`, `SYNTH_CONDITIONS` — fixed lists
  of fabricated names ("Dr. A. Rivera", "Example Health Plan", "Drug A", "Example
  Condition", etc.).
- `SYNTH_DATES` — 36 fake 2025 dates (3 days × 12 months).
- `SYNTH_LAB_NAMES` — 5 lab test names.

### Split/family constants

- `SPLITS = ("train", "val", "test", "challenge")`.
- `NOISE_RATE = 0.25` — probability a non-challenge document gets one sentence
  borrowed from a different class ("cross-class noise").
- `FAMILY_COUNT = 10` — number of distinct template "families" (layout styles).
- `FAMILY_SPLITS` — maps family index → split: families `0–6 → train`, `7 → val`,
  `8 → test`, `9 → challenge`. The comment explains why: since a family is never
  shared across splits, the benchmark measures generalization to an **unseen
  layout**, not memorization of one seen during fitting.
- `FAMILY_FRAMES` — 10 `(header, footer, separator)` tuples, one per family, giving
  each family a distinct document "frame" (e.g. `("DOCUMENT INTAKE", "End of intake
  record", " | ")`) that simulates different real-world templates/scanners.

### Small RNG helpers

`_p`, `_prov`, `_payer`, `_drug`, `_cond`, `_date` — each takes a `random.Random` and
returns `rng.choice(...)` over the matching vocabulary list (patient, provider,
payer, drug, condition, date respectively).

### `TEMPLATES`

A dict keyed by each `DocumentType.value`, mapping to a list of `lambda(r) -> str`
sentence generators specific to that class:

| Class | Sentence themes |
|---|---|
| `pa_request` | drug/prescriber/diagnosis, health plan review request, quantity/date, step-therapy policy citation, attached documentation. |
| `insurance_card` | member ID card, group number, RxBIN/PCN/GRP, customer service numbers, plan type, coverage disclaimer. |
| `referral` | referral to a specialty program, referring/receiving provider, reason for referral, scheduling request. |
| `prescription` | Rx/Sig line, prescriber + DEA, patient + date, quantity/refills, pharmacy/NPI, substitution note. |
| `clinical_note` | progress note, assessment/plan, subjective/objective, visit date + follow-up. |
| `medication_history` | med history header, start/stop dates, weeks of therapy, adherence, prior auth on file, allergies. |
| `lab_report` | lab report header, a named result value, ordering provider, reference range note, specimen type, release note. |
| `other` | billing correspondence, appointment reminder, fax cover sheet, thank-you letter, office hours notice. |

`LABELS = list(TEMPLATES.keys())` — the 8 class strings in this fixed order.

### `SHARED_POOL`

Six generic, cross-class boilerplate sentence generators (e.g. "Please retain a copy
of this document for your records.", a fake case reference number, "On file with
{payer}."). The comment explains the intent: mixing generic boilerplate into every
class keeps the dataset from being trivially separable on class-unique vocabulary
alone, closer to how real scanned forms actually read (mostly headers/footers/legal
text, with the identifying content in a small fraction of it).

### Functions

- **`_apply_ocr_like_noise(text, rng) -> str`** — simulates OCR character
  confusion. Builds a substitution table (`o→0`, `i→1`, `e→c`, `l→1`, `s→5`); for
  every character, with 3.5% probability substitutes it (preserving the original
  case via `.isupper()`), otherwise keeps it unchanged. Used only for the challenge
  split.
- **`_generate_doc_text(label, rng, family, case_index) -> str`** — builds one
  document's full text:
  1. `k = rng.randint(3, min(5, len(pool)))` — total sentence count.
  2. `n_shared = rng.randint(1, min(3, k - 1))` — how many of those `k` sentences
     come from `SHARED_POOL` rather than the class's own templates (most of a real
     form is boilerplate; only a couple of sentences carry the actual signal).
  3. Samples (without replacement) and renders `k - n_shared` class-specific
     sentences and `n_shared` shared sentences, and concatenates them.
  4. `noise_rate` is `0.70` for the challenge family, else the global `0.25` —
     challenge documents get far more cross-class contamination.
  5. With probability `noise_rate`, picks a random *other* label and inserts one of
     its sentences at a random position — the deliberate "noise" sentence.
  6. `rng.shuffle(sentences)` — randomizes order so a classifier can't rely on
     sentence position.
  7. Assembles `header\n{sentences joined by separator}\nfooter\n` using this
     family's frame.
  8. A regex normalizes any `SYNTH-####` patient-ID text that leaked in from a
     template call to the one deterministic ID for this case
     (`SYNTH-{case_index+1:04d}`), so one synthetic identity belongs to exactly one
     case.
  9. If this is the challenge family, applies `_apply_ocr_like_noise` before
     returning.
- **`build_dataset(out_dir, per_class, seed, rendered_per_class=0) -> Path`** — the
  main generator.
  1. Validates `per_class >= FAMILY_COUNT` (every family/split needs at least one
     doc) and `0 <= rendered_per_class <= per_class`.
  2. Sets up `documents/`, `rendered/`, `manifest.csv`, and
     `ingestion_manifest.csv` under `out_dir`.
  3. For each label, clears any stale `doc_*.*` files left from a previous run
     (so re-running with a smaller `--per-class` doesn't leave orphaned files —
     keeps output fully reproducible for a given seed/count).
  4. For each document index `i` in `range(per_class)`:
     - `family = i % FAMILY_COUNT` (round-robins across families in creation
       order); `split` is looked up from `FAMILY_SPLITS`.
     - `doc_rng = random.Random(f"{seed}:{label}:{i}")` — a **per-document** seed
       derived from `seed`, `label`, and `i`, so document `i` is byte-identical
       across runs regardless of iteration order.
     - Generates the text, writes it to `doc_{i:04d}.txt`, and appends a
       `manifest.csv` row: `doc_id`, `filename`, forward-slash-normalized
       `relative_path`, `label`, `split`, `char_count`, `case_id`
       (`SYNTH-CASE-####` — used later for the leakage check),
       `template_family_id`, and `degradation` (`"ocr_like_text"` for challenge
       docs, else `"none"`).
     - If `i < rendered_per_class`: lazily imports `render_text_image`/
       `render_text_pdf` (deferred so the heavy render deps aren't required just to
       build the plain-text corpus), picks one of 5 degradations round-robin
       (`i % 5`), renders a "clean" PDF and a degraded PNG with a seed hashed from
       the same per-doc seed string, and appends two `ingestion_manifest.csv` rows
       (one for the PDF, one for the image) recording the asset path and the
       matching ground-truth text path — this is what `benchmark_ingestion.py`
       later reads.
  5. Writes both CSV manifests (`csv.DictWriter`, explicit `fieldnames`,
     `lineterminator="\n"` for cross-platform consistency) and returns the
     classification manifest's path.
- **`main()`** — the `rxauth-build-dataset` CLI: `--output-dir` (default `./data`),
  `--per-class` (default `60`), `--rendered-per-class` (default `2`), `--seed`
  (default `42`). Calls `build_dataset` and prints the total document count
  (`per_class × 8`), the rendered sample count (`rendered_per_class × 8 × 2`), and
  the manifest path.

---

## `classifier.py`

The leakage-resistant TF-IDF + Logistic Regression classifier benchmark.

### Imports

`csv`, `pickle`, `time`; `from dataclasses import dataclass, field`; `from pathlib
import Path`; `from typing import Any`; `numpy as np`; from `sklearn`:
`TfidfVectorizer`, `LogisticRegression`, `accuracy_score`, `classification_report`,
`confusion_matrix`, `f1_score`; `from .ingestion import ingest_document`; from
`.models`: `Document`, `DocumentType`.

### `DatasetSplit` (`@dataclass`)

Six parallel lists (`texts`, `labels`, `filenames`, `case_ids`,
`template_family_ids`, `degradations`), each defaulted via
`field(default_factory=list)`. `__len__` returns `len(self.texts)`, so a
`DatasetSplit` supports `len(split)` and truthiness checks directly.

### `DocumentPrediction` (`@dataclass(frozen=True)`)

Immutable result of one prediction: `label`, `confidence`, `requires_human_review`.

### `DocumentClassifier` (`@dataclass`)

`vectorizer: TfidfVectorizer`, `model: LogisticRegression`,
`confidence_threshold: float = 0.65`.

- **`predict_text(text) -> DocumentPrediction`** — transforms `text` through the
  fitted vectorizer, takes `predict_proba(...)[0]`, finds the arg-max class index,
  reads its probability as `confidence`, looks up the label string via
  `model.classes_[best_index]`, and flags `requires_human_review` when `confidence <
  self.confidence_threshold`.
- **`classify_path(path, *, document_id) -> tuple[Document, bool]`** — ingests the
  file via `ingest_document` (reusing the Phase 1.5 ingestion boundary — the same
  path a real document would take), predicts on the extracted text, builds a
  `Document` (parsing the label back into `DocumentType`, using the ingested page
  count), and returns `(document, requires_human_review)`.
- **`save(path) -> None`** — ensures the parent directory exists and pickles the
  whole classifier (vectorizer + model + threshold) to disk.
- **`load(path) -> DocumentClassifier`** (`classmethod`) — unpickles the file (the
  inline comment/`noqa` flags that loading a pickle is only safe from a **trusted
  build** — arbitrary code can execute during unpickling) and raises `TypeError` if
  the loaded object isn't actually a `DocumentClassifier`.

### `validate_split_isolation(splits) -> None`

For every unordered pair of named splits, checks whether their `case_ids` or
`template_family_ids` sets overlap; if so, raises `ValueError` naming up to 3
example overlapping IDs/families. This is the concrete leakage guard behind the
"cases and template families are mutually exclusive across every split" claim in the
generated report.

### `load_manifest(data_dir) -> dict[str, DatasetSplit]`

1. Reads `manifest.csv` (`FileNotFoundError` with a "run `rxauth-build-dataset`
   first" hint if missing).
2. Validates the CSV header contains every required column, raising `ValueError`
   naming any that are missing (a compatibility check against the current manifest
   contract).
3. For each row: validates `split` is one of the four known names, calls
   `ingest_document` on the file (so training data flows through the *same*
   text-extraction path a production document would), and appends the row's fields
   to the matching `DatasetSplit`.
4. After the loop, verifies every split ended up non-empty (`ValueError` naming any
   empty ones), then calls `validate_split_isolation` before returning the dict.

### `_expected_calibration_error(labels, predictions, confidences, bins=10) -> float`

Computes Expected Calibration Error, a standard measure of whether a model's
confidence scores are trustworthy:

1. `correctness` — a boolean array of `truth == prediction`
   (`zip(..., strict=True)` guards against mismatched-length inputs).
2. Builds `bins` equal-width confidence-bin edges from 0 to 1
   (`np.linspace(0, 1, bins + 1)`).
3. For each bin, selects the predictions whose confidence falls in `(lower, upper]`,
   computes that bin's accuracy and mean confidence, and accumulates
   `bin_weight * |accuracy - confidence|` — the standard ECE formula. Empty bins are
   skipped.

### `_evaluate_split(classifier, split, labels) -> dict`

1. Transforms `split.texts` through the fitted vectorizer.
2. Times `predict_proba` with `time.perf_counter()` for a latency measurement.
3. Computes per-row arg-max predictions and their confidences.
4. `latency_ms_per_doc = (elapsed / len(split)) * 1000`.
5. Returns a dict of: `accuracy`, `macro_f1` (`zero_division=0` so an absent class
   doesn't warn/crash), the full sklearn `classification_report` string, the
   `confusion_matrix`, `latency_ms_per_doc`, `mean_confidence`,
   `expected_calibration_error`, `review_rate` (fraction of predictions below
   `confidence_threshold` — how often the model punts to a human), and
   `misclassified` — a list of `(filename, truth, prediction, 90-char snippet)`
   tuples for every wrong prediction, used to print concrete failure examples later.

### `train_and_evaluate(splits, *, confidence_threshold=0.65) -> dict`

1. `labels = sorted({... across every split ...})` — ensures the confusion matrix
   and report columns are complete even if a class is rare or absent in one split.
2. Builds a `TfidfVectorizer` (lowercase, English stopwords, unigrams+bigrams
   `(1, 2)`, `min_df=2` to drop ultra-rare/noise tokens, `sublinear_tf=True` to
   dampen very frequent terms) and **fits it on the train split only** — vocabulary
   never sees val/test/challenge text, preventing leakage.
3. Trains a `LogisticRegression` (`max_iter=1000` for convergence on the
   high-dimensional sparse TF-IDF features, `class_weight="balanced"` to offset any
   class-size imbalance, `random_state=42` for reproducibility).
4. Wraps everything into a `DocumentClassifier`.
5. Computes `train_accuracy` on the training set itself (a fit diagnostic, not a
   generalization metric).
6. Runs `_evaluate_split` for `val`, `test`, and `challenge`.
7. Returns a results dict bundling the classifier, raw vectorizer/model, `labels`,
   `train_accuracy`, the full `evaluations` dict, convenience top-level aliases
   pulled from the `test` split (`test_accuracy`, `classification_report`,
   `confusion_matrix`, `latency_ms_per_doc`, `misclassified`), and `n_<split>`
   counts for every split.

### `_render_evaluation(lines, name, evaluation) -> None`

Mutates the passed `lines` list in place, appending a Markdown section for one
split's metrics (accuracy, macro F1, mean confidence, ECE, review rate, latency)
plus its `classification_report` inside a fenced code block.

### `render_report_md(results, data_dir) -> str`

Builds the full Markdown benchmark report: a "Dataset contract" section (manifest
source, split sizes, class list, the leakage-free-splits and challenge-split notes,
and a synthetic-data disclaimer); a "Training" section (train accuracy, "vectorizer
fit: train split only", the confidence threshold); the three per-split metric
sections via `_render_evaluation`; a Markdown confusion-matrix table for the test
split (rows = true label, columns = predicted, one row per label built from the
`confusion_matrix` array); a "Challenge failure cases" table listing up to 20
misclassified challenge examples (or "None on this challenge split." if there are
none); and a closing "Interpretation" paragraph noting the grouped test set is the
primary comparison point, the challenge set is for robustness analysis rather than
model selection, and image OCR quality depends on the configured backend.

---

## `train_classifier.py`

The `rxauth-train-classifier` CLI.

### Imports

`from __future__ import annotations`; `argparse`; `from pathlib import Path`; `from
.classifier import load_manifest, render_report_md, train_and_evaluate`.

### `main()`

1. Parses `--data-dir` (default `./data`), `--output-dir` (default `./reports`),
   `--artifact-path` (default `./artifacts/classifier_baseline.pkl`), and
   `--confidence-threshold` (default `0.65`, validated into `[0, 1]` via
   `parser.error(...)` otherwise).
2. `load_manifest` → `train_and_evaluate` → `render_report_md`.
3. Writes the report to `<output-dir>/classifier_baseline.md`, saves the classifier
   bundle to `--artifact-path` via `.save(...)`, and prints the report plus both
   output paths.

---

## `deep_classifier.py`

The Phase 2 transformer experiment. It fine-tunes a Hugging Face sequence classifier
through a small explicit PyTorch loop, evaluates it under the exact same split and
metric contract as the classical baseline, persists it without pickle, and renders the
paired scientific comparison. PyTorch and Transformers are optional dependencies:
importing this module, the package, or the classical pipeline does not install or load
them.

### Imports

| Import | Why |
|---|---|
| `json` | Writes and reads the versioned RxAuth metadata beside the Hugging Face artifact. |
| `platform` | Records the selected CPU/accelerator description in the generated benchmark report. |
| `random`, `numpy` | Seed Python and NumPy and perform arg-max/mean calculations during evaluation. |
| `time` | Measures fine-tuning time and model-only inference latency. |
| `statistics.mean`, `statistics.stdev` | Aggregates repeat-seed metrics with sample standard deviation. |
| `dataclass` | Defines immutable experiment/dependency configuration and the inference wrapper. |
| `import_module` | Loads optional `torch` and `transformers` only when deep training or inference is requested. |
| `Path`, `Any` | Filesystem-safe artifact paths and intentionally opaque third-party model/tensor types. |
| sklearn metrics | Keeps accuracy, macro F1, classification report, and confusion matrix definitions identical to the baseline. |
| `DatasetSplit`, `DocumentPrediction`, `_expected_calibration_error` | Reuses the baseline's data, output, and calibration contracts instead of creating incompatible Phase 2 versions. |
| `ingest_document`, `Document`, `DocumentType` | Gives path inference the same ingestion boundary and typed domain output as the baseline. |

### `DeepLearningDependencyError(RuntimeError)`

A dedicated failure type for a missing optional runtime. `_load_deep_dependencies`
raises it with the actionable command `uv sync --extra deep --group dev`; callers can
distinguish an installation problem from a training/data error.

### `DeepTrainingConfig` (`@dataclass(frozen=True)`)

The complete configuration for one reproducible fine-tuning run:

- `model_name="distilbert-base-uncased"` — the initial small pretrained candidate;
- `epochs=4`, `batch_size=16`, `learning_rate=2e-5`, `weight_decay=0.01` — optimizer
  and training-loop controls;
- `max_length=256` — tokenizer truncation ceiling;
- `seed=42` — shared Python/NumPy/PyTorch seed;
- `early_stopping_patience=2` — stop after this many validation epochs without a
  strictly better macro F1;
- `device="auto"` — prefer CUDA, then Apple MPS, otherwise CPU.

`__post_init__` rejects an empty model name, non-positive epochs/batch size/learning
rate, negative weight decay, fewer than 8 tokens, patience under 1, or a device outside
`auto/cpu/cuda/mps`. Because the dataclass is frozen, a recorded configuration cannot
silently change partway through an experiment.

### Optional-runtime helpers

- **`_DeepDependencies` (`@dataclass(frozen=True)`)** — holds the imported `torch`
  module plus Hugging Face's `AutoTokenizer` and
  `AutoModelForSequenceClassification` factories. The rest of the code receives this
  small bundle rather than importing optional packages globally.
- **`_load_deep_dependencies() -> _DeepDependencies`** — dynamically imports
  `torch` and `transformers`, normalizes either missing-package failure into
  `DeepLearningDependencyError`, and returns the three runtime handles.
- **`_resolve_device(torch, requested) -> str`** — for `auto`, chooses CUDA when
  available, then MPS, then CPU. An explicitly requested unavailable accelerator is an
  error rather than a silent CPU fallback, keeping benchmark hardware claims honest.
- **`_seed_everything(torch, seed) -> None`** — seeds Python, NumPy, CPU PyTorch, and
  all CUDA devices. It also asks PyTorch for deterministic algorithms with
  `warn_only=True`: nondeterministic operations are surfaced without making the
  experiment unusable on a backend lacking a deterministic implementation.
- **`_hardware_description(torch, device) -> str`** — records the CUDA device name,
  Apple Silicon architecture, or CPU description so latency results name the hardware
  that produced them.

### `_EncodedDataset`

A minimal object implementing the protocol expected by `torch.utils.data.DataLoader`:

- `encoded` stores tokenizer tensors such as `input_ids` and `attention_mask`;
- `label_ids` stores the integer target tensor;
- `__len__` returns the number of labels;
- `__getitem__` selects one row from every tokenizer tensor and adds it under the
  key `labels`, which Hugging Face models recognize and use to calculate supervised
  cross-entropy loss.

It deliberately does not subclass a PyTorch type, which allows the module itself to be
imported and tested when the optional `deep` extra is absent.

### `DeepDocumentClassifier` (`@dataclass`)

The Phase 2 inference bundle: `tokenizer`, fine-tuned `model`, ordered `labels`, source
`model_name`, selected `device`, `max_length`, and `confidence_threshold` (default
`0.65`). Its public interface mirrors `DocumentClassifier` where the two models need
to be interchangeable.

- **`predict_text(text) -> DocumentPrediction`** — loads the optional runtime,
  tokenizes one string with padding/truncation, moves tensors to the selected device,
  switches the model to evaluation mode, and runs it under
  `torch.inference_mode()` (no gradient graph). Softmax converts logits to class
  probabilities; arg-max selects the ordered label and its confidence; confidence
  below the threshold sets `requires_human_review=True`.
- **`classify_path(path, *, document_id) -> tuple[Document, bool]`** — ingests the
  source file, calls `predict_text`, and returns the same typed `Document` plus review
  flag as the classical classifier. This keeps downstream code independent of the
  chosen classifier family.
- **`save(path) -> None`** — creates an artifact directory and delegates weights,
  config, and tokenizer files to Hugging Face's `save_pretrained`. It separately
  writes `rxauth_metadata.json` with `artifact_format=1`, ordered labels, original
  model name, token limit, and review threshold. Unlike the baseline pickle, this
  does not deserialize arbitrary Python objects.
- **`load(path, *, device="auto") -> DeepDocumentClassifier`** — requires the
  metadata file, rejects unknown artifact versions, resolves the runtime device,
  loads tokenizer/model from the local directory, moves the model to that device,
  and reconstructs the wrapper from persisted settings.

### Dataset and evaluation helpers

- **`_make_dataset(split, *, tokenizer, label_to_id, max_length, torch)`** — tokenizes
  every text in a split with padding/truncation and `return_tensors="pt"`, maps the
  string labels to deterministic integer IDs, and returns `_EncodedDataset`.
- **`_evaluate_split(classifier, split, labels, *, batch_size, torch) -> dict`** —
  creates a non-shuffled `DataLoader`, runs batches in evaluation/inference mode,
  concatenates CPU probabilities, and calculates the same fields as the baseline:
  accuracy, macro F1, classification report, confusion matrix, mean confidence, ECE,
  review rate, and concrete misclassification tuples. Timing begins after tokenization,
  so the reported transformer number is model execution per document, comparable in
  definition to the baseline timing that begins after vectorization. The report still
  warns that latency is meaningful only when both models run on the same machine.

### `train_and_evaluate_deep(...) -> dict`

The full Phase 2 fine-tuning/evaluation flow:

1. Validates the review threshold, creates a default config when none is supplied,
   loads the optional runtime, seeds it, and resolves the requested device.
2. Builds one sorted label vocabulary across all splits and both `label_to_id` and
   `id_to_label` mappings. Stable ordering is required for logits, reports, and saved
   artifacts to agree.
3. Loads the configured pretrained tokenizer and sequence-classification model with
   the corpus's 8 labels. `ignore_mismatched_sizes=True` intentionally reinitializes a
   checkpoint's old classification head when its class count differs while retaining
   compatible pretrained encoder weights.
4. Wraps the tokenizer/model into `DeepDocumentClassifier`, encodes only the training
   split for optimization, and creates a seeded shuffled `DataLoader`.
5. Uses `torch.optim.AdamW` with the configured learning rate/weight decay. For every
   batch: clears gradients, moves tensors to the device, obtains Hugging Face's
   supervised `output.loss`, backpropagates, clips gradient norm to `1.0`, steps the
   optimizer, and accumulates mean epoch loss.
6. After each epoch, evaluates **validation only**. A strictly higher validation macro
   F1 becomes the best checkpoint: every state-dict tensor is detached, cloned, and
   held on CPU. Otherwise the no-improvement counter advances and can trigger early
   stopping. A progress line prints epoch, mean train loss, and validation macro F1 so
   long CPU runs remain observable. Neither test nor challenge data can select a checkpoint.
7. Restores the best validation checkpoint, evaluates validation/test/challenge, then
   evaluates train once as a fit diagnostic.
8. Returns the classifier, labels/config/device/hardware, learning history, best epoch, total
   training seconds, train accuracy, all split evaluations, and split sizes. Test is
   the held-out comparison; challenge remains error/robustness analysis only.

### Reporting utilities

- **`artifact_size_mb(path) -> float`** — measures one artifact file or recursively
  sums a Hugging Face artifact directory, returning MiB; raises `FileNotFoundError`
  for a missing path.
- **`render_comparison_report(baseline, deep, *, data_dir,
  baseline_artifact_mb, deep_artifact_mb, deep_runs=None) -> str`** — builds
  `classifier_deep_vs_baseline.md`: experiment/split guarantees, transformer
  hyperparameters, paired val/test/challenge metrics, artifact/device cost, training
  history, test failures/classification report/confusion matrix, and up to 20 challenge
  failures. With multiple runs it adds per-seed metrics plus mean/sample-standard-
  deviation tables and computes the interpretation from mean test macro F1. The
  selected run is still the one chosen by validation macro F1 only. The checked-in
  three-seed report supports the Phase 2 decision to retain the classical baseline;
  the renderer records evidence and tradeoffs rather than hard-coding that decision.

---

## `train_deep_classifier.py`

The `rxauth-train-deep-classifier` CLI. It is kept separate from
`train_classifier.py` because invoking Phase 2 may download a pretrained model and
requires the optional `deep` dependency group.

### Imports

`argparse`, `gc`, `Path`; from `.classifier`: `load_manifest`, `train_and_evaluate`; from
`.deep_classifier`: `DeepTrainingConfig`, `artifact_size_mb`,
`render_comparison_report`, `train_and_evaluate_deep`.

### `main()`

1. Parses paths for data, reports, the deep artifact directory, and the paired
   baseline pickle. It also exposes model name, epochs, batch size, learning rate,
   weight decay, max tokens, mutually exclusive `--seed`/`--seeds`, early-stopping
   patience, device, and the shared confidence threshold.
2. Validates the threshold into `[0,1]`, defaults to seed 42 when neither seed option
   is supplied, and rejects duplicate repeat-seed values.
3. Loads the manifest **once**, so both candidates receive the exact same in-memory
   `DatasetSplit` objects.
4. Trains and saves a fresh classical baseline for a genuinely paired run rather than
   comparing against stale/copied metrics.
5. For each seed, constructs a validated `DeepTrainingConfig`, fine-tunes/evaluates the
   transformer, and keeps a metric-only summary. When a run exceeds the current best
   validation macro F1, its classifier replaces the saved artifact. Test/challenge
   metrics never participate in that selection. `gc.collect()` releases each full
   in-memory model before the next seed.
6. Measures both completed artifacts and renders the selected run plus every seeded
   summary, writes
   `<output-dir>/classifier_deep_vs_baseline.md`, and prints the report plus output
   paths.

Install and run it with:

```bash
uv sync --extra deep --group dev
uv run rxauth-train-deep-classifier --seeds 7 42 73
```

The core install/CI continues to use `uv sync --group dev`; because `torch` and
`transformers` live under `[project.optional-dependencies].deep`, the default workflow
does not pay their download, environment-size, or import-startup cost.

### Phase 2 tests

`tests/test_deep_classifier.py` stays dependency-light so default CI does not install
PyTorch merely to validate the experiment harness:

- invalid epochs/devices exercise `DeepTrainingConfig` validation;
- monkeypatching `import_module` to raise `ImportError` verifies the optional-runtime
  failure includes the exact installation command;
- one file and one nested directory verify `artifact_size_mb` uses MiB and recursive
  directory sizing correctly;
- small fabricated result dictionaries verify the paired report records validation-only
  selection, both model names, the single-seed warning, and a correctly signed F1 delta;
- a two-seed fixture verifies seed listing, mean/sample-standard-deviation aggregation,
  and the multi-seed interpretation.

These tests cover dependency-free control logic. The implementation was manually
smoke-tested through train/save/load/infer, and the real three-seed experiment exercised
the full runtime. A permanent deep-extra CI job is intentionally omitted because the
deep candidate was rejected and remains optional.

---

## `benchmark_extraction.py`

The `rxauth-benchmark-extraction` CLI and evaluation library. It measures the
deterministic extractor against `data/extraction_gold.jsonl` at field, normalization,
provenance, review-routing, and latency levels. Expected labels are never inferred from
the rules being evaluated.

### Imports

| Import | Why |
|---|---|
| `argparse`, `Path` | Parse CLI paths/threshold and write the generated report. |
| `time` | Measure extraction-only elapsed time per document. |
| `Counter` | Perform duplicate-safe multiset matching of exact expected/predicted fields. |
| `Any`, `Literal` | Type heterogeneous signatures/results and restrict gold splits. |
| `BaseModel`, `Field` | Validate every JSONL row and safely default expected-field lists. |
| extraction constants/function | Run the exact version and confidence threshold being reported. |
| `IngestedDocument`, `IngestedPage` | Wrap gold text in the same page-level contract consumed by extraction. |
| `Evidence` | Share normalization/signature logic between predictions and gold fields. |

### Gold models

- **`GoldEvidence(BaseModel)`** — expected evidence type plus optional medication,
  text/numeric value, unit, outcome, an exact hand-authored `source_text`, and
  `requires_review` (default `False`). Confidence is intentionally absent: gold says
  whether review is required, not which arbitrary score a rule must emit.
- **`GoldDocument(BaseModel)`** — unique `document_id`, `split` restricted to
  `validation`/`test`, synthetic filename/text, and zero or more expected fields.

The companion [extraction dataset card](extraction-gold.md) documents the 45 records,
schema, synthetic-only guardrail, coverage, the disclosed test-to-validation move after
the initial negated-diagnosis failure, and the frozen-rule first run of eight new test records.

### `load_gold(path) -> list[GoldDocument]`

1. Requires the JSONL file to exist and parses each nonblank line with
   `GoldDocument.model_validate_json`, adding the line number to malformed-JSON errors.
2. Rejects duplicate document IDs.
3. Requires every expected `source_text` to occur **exactly once** in its document.
   This makes the expected start/end offsets unambiguous without deriving the source
   phrase from extractor output.
4. Rejects an empty corpus or one missing either validation or test records.

### Signature and metric helpers

- **`_normalized_signature(item)`** — tuple of evidence type, medication, text value,
  numeric value, unit, and outcome.
- **`_exact_signature(item)`** — appends cited source text to the normalized tuple.
  `_evaluate_records` additionally prefixes `document_id`; therefore an error in one
  document cannot cancel an opposite error in another with the same text/value.
- **`_safe_divide`** — returns `0.0` for a zero denominator rather than crashing on a
  split with no positives.
- **`_f1`** — harmonic mean through `_safe_divide`.
- **`_format_signature`** — renders only non-`None` tuple fields into readable failure
  details for the Markdown report.

### `_evaluate_records(records, *, confidence_threshold) -> dict`

For every gold document:

1. Wraps its text as one confidence-1.0 `IngestedPage`, times `extract_evidence`, and
   collects issue IDs.
2. Adds gold/predicted exact signatures to document-scoped `Counter` objects.
3. Aligns fields by `(evidence_type, source_text)`. For aligned fields it separately
   checks normalized-value equality and exact page/start/end/source provenance.
4. Builds evidence-level expected/predicted review keys and document-level review sets.

After all documents it uses Counter intersection/subtraction for true positives, false
positives, and false negatives, then calculates:

- exact field precision, recall, and F1;
- normalized-value accuracy and provenance-span accuracy over aligned fields;
- evidence review precision, recall, and F1;
- document review-routing accuracy;
- extraction latency per document;
- concrete false-positive/false-negative signatures.

### Public functions and CLI

- **`benchmark_extraction(gold_path, *, confidence_threshold=...)`** — validates the
  threshold, loads gold once, evaluates validation and test independently, and returns
  extractor/version/corpus metadata plus both result dictionaries.
- **`render_report(results, gold_path) -> str`** — writes the contract, a paired metrics
  table, per-split failures, and definitions/limitations. The checked-in report states
  corpus size and synthetic status beside the perfect current score.
- **`main()`** — parses `--gold-path`, `--output-dir`, and threshold; runs the benchmark;
  writes `reports/extraction_benchmark.md`; and prints report/output path.

### Benchmark tests

`tests/test_benchmark_extraction.py` contains four focused checks:

- a perfect diagnosis/vague-duration fixture must score 1.0 for exact fields, values,
  spans, and routing;
- an unsupported `HbA1c` phrasing must appear as a measurable false negative;
- document-scoped signatures prevent a false positive and false negative with otherwise
  identical signatures from cancelling across documents;
- the loader rejects duplicate IDs and source strings occurring more than once.

---

## `benchmark_ingestion.py`

The `rxauth-benchmark-ingestion` CLI: reproducible evaluation of the Phase 1.5
ingestion boundary against the rendered PDF/PNG corpus.

### Imports

`from __future__ import annotations`; `argparse`; `csv`; `re`; `from collections.abc
import Callable`; `from pathlib import Path`; `from typing import Any`; `from
.ingestion import ingest_document, preprocess_image`.

### Functions

- **`_normalize(text) -> str`** — `re.sub(r"\s+", " ", text).strip()`: collapses
  every run of whitespace to a single space and trims the ends, so comparisons
  aren't thrown off by incidental formatting differences.
- **`_edit_distance(left, right) -> int`** — classic Levenshtein distance via the
  space-optimized DP algorithm (keeping only the "previous row" instead of a full
  2D table):
  1. `previous` starts as `[0, 1, 2, ..., len(right)]` — the cost of turning the
     empty string into each prefix of `right`.
  2. For each character of `left`, builds a `current` row starting at
     `left_index` (cost of turning that prefix of `left` into the empty string),
     then for each character of `right` takes the minimum of insertion
     (`current[-1] + 1`), deletion (`previous[right_index] + 1`), and substitution
     (`previous[right_index - 1]` plus `0` or `1` depending on whether the
     characters match).
  3. Returns the last row's final value — the total edit distance.
- **`character_error_rate(expected, actual) -> float`** — normalizes both strings;
  returns `0.0` if both are empty, `1.0` if `expected` is empty but `actual` isn't
  (all extra characters count as complete error since there's no length to divide
  by), otherwise `edit_distance / len(expected)` — the standard CER metric.
- **`benchmark_ingestion(data_dir, *, ocr_backend=None, run_ocr=False) -> dict`**
  1. Reads `ingestion_manifest.csv` (`FileNotFoundError` telling the user to
     rebuild the dataset if missing; `ValueError` if the manifest is empty — i.e.
     built with `--rendered-per-class 0`).
  2. For each row, reads the ground-truth text file. If `source_format == "pdf"`,
     runs `ingest_document` on the real asset and records its `character_error_rate`
     against the ground truth. If `"image"`:
     - Always runs `preprocess_image` alone first (so image normalization/OpenCV
       correctness is validated even without any OCR engine installed); any
       exception is caught broadly (documented as intentional — it keeps the
       benchmark run alive across many images while recording which filenames
       failed) and the path is appended to `image_preprocessing_failures`, then
       `continue`s to the next row.
     - If preprocessing succeeded and either a custom `ocr_backend` was injected or
       `--run-ocr` was passed, additionally runs the full `ingest_document` with
       OCR and records its CER.
     Any other `source_format` value raises `ValueError` (manifest corruption
     guard).
  3. Computes: `image_count`; `image_preprocessing_success_rate` (`None` if there
     were zero images); and the mean CER across whichever PDF/OCR rates were
     actually collected (`None` if none were measured, e.g. OCR wasn't run).
  4. Returns a dict with `documents_total`, `pdf_documents`,
     `pdf_mean_character_error_rate`, `image_documents`,
     `image_preprocessing_success_rate`, `image_preprocessing_failures` (list),
     `ocr_documents`, `ocr_mean_character_error_rate`.
- **`render_report(results) -> str`** — builds the Markdown report: corpus stats
  (rendered asset count, PDF/image counts, a "no PHI" disclaimer), a Results section
  with PDF CER and preprocessing success rate (or "not measured" placeholders when
  `None`), and either a note explaining OCR CER wasn't measured (the benchmark
  supports an injectable backend and optional Tesseract, but doesn't require either)
  or the actual OCR CER value — finally a one-line definition of character error
  rate.
- **`main()`** — parses `--data-dir` (default `./data`), `--output-dir` (default
  `./reports`), `--run-ocr` (flag). Runs `benchmark_ingestion`, renders the report,
  writes it to `<output-dir>/ingestion_benchmark.md`, and prints the report plus the
  output path.

---

## `__init__.py`

```python
"""Core package for the RxAuth AI prototype."""

__version__ = "0.1.0"
```

Marks `rxauth_ai` as a package and exposes its version string — nothing else is
re-exported here, so every other module is imported explicitly (e.g. `from
rxauth_ai.models import Case`) rather than through the package root.
