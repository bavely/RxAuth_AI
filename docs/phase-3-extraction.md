# Phase 3 — information extraction with confidence

Phase 3 converts ingested document text into normalized `Evidence` records without losing the
source needed to audit each value. The first slice is deliberately deterministic: it establishes
the typed contract, provenance rules, review routing, and evaluation seams before adding a learned
NLP model.

## Current implementation

`rxauth_ai.extraction` currently recognizes the synthetic corpus's explicit forms of:

- confirmed diagnosis (`Diagnosis:` and `Assessment:`), excluding `No Diagnosis:`;
- numeric prior-therapy duration in days, weeks, or months;
- adequate, inadequate, absent, or good prior-therapy response;
- A1c numeric values;
- prescriptions beginning with `Rx:`;
- labeled patient/member IDs and payer names, plus payer-card headings;
- requested days supply, prescription quantity, and ISO-formatted document dates;
- LDL cholesterol, ALT, eGFR, and CRP numeric values;
- attached screening-document statements;
- ambiguous phrases such as “on therapy for several months.”

Each `Evidence` item records:

- a deterministic document-scoped evidence ID;
- normalized evidence type, text/numeric value, medication, unit, and outcome where present;
- extraction confidence and `regex-v1` extractor version;
- document ID, filename, page, exact source text, and inclusive/exclusive page-character offsets.

An extraction below the configurable confidence threshold is retained with its original source
but also produces an `ExtractionIssue`. It is never silently discarded or converted into a more
specific value than the source supports.

Run it with:

```bash
uv run rxauth-extract data/documents/clinical_note/doc_0002.txt --document-id SYN-EXAMPLE
```

The CLI returns JSON containing all evidence, review issues, and a top-level
`requires_human_review` flag.

## Gold benchmark

The first evaluation boundary is now implemented:

```bash
uv run rxauth-benchmark-extraction
```

It reads `data/extraction_gold.jsonl` and writes `reports/extraction_benchmark.md`. The
45-document corpus has 25 validation and 20 refreshed test documents. The benchmark reports:

- document-scoped exact field precision, recall, and F1;
- normalized-value accuracy for fields aligned by type and source;
- exact page/character provenance-span accuracy;
- evidence-level review precision/recall/F1 and document review accuracy;
- extraction latency and concrete false-positive/false-negative details.

`regex-v1` currently scores 1.000 across these metrics on both splits. This means the current
rules satisfy the small, explicit synthetic contract; it does not demonstrate robustness to real
clinical writing, OCR variation, unseen terminology, or independent authorship. See
[the extraction dataset card](extraction-gold.md) for coverage and the disclosed validation/test
refresh after the first run exposed a negated-diagnosis false positive. The newest eight test
documents were added only after the administrative/date/quantity/lab/screening rules were frozen;
they passed on their first run without rule changes.

## Design boundaries

- Extraction consumes `IngestedDocument`; it does not duplicate text/PDF/OCR handling.
- Character offsets are page-relative and use Python's inclusive-start/exclusive-end convention.
- One evidence record has one exact source span. Facts from separate spans are not merged yet,
  because the current `Evidence` contract cannot cite multiple sources for one normalized fact.
- No LLM or external clinical terminology service is used in this first slice.
- Patterns intentionally cover only explicit synthetic forms and make no production-validity
  claim.

## Work required to complete §9

1. Expand medication-name recognition beyond the synthetic `Drug A`–`Drug Z` vocabulary and
   define an explicit normalization strategy. Dates, quantities, payer/member identifiers,
   additional labs, and screening-document presence now have benchmarked baseline coverage.
2. Extend the evidence contract to support multiple provenance spans, then combine duration and
   outcome statements only when both cited spans refer to the same medication/document context.
3. Define precedence and deduplication when multiple patterns identify overlapping spans.
4. Expand the gold set with independently authored paraphrases, OCR corruption, multi-page cases,
   negation, and harder cross-class distractors before treating test performance as generalization.
5. Calibrate confidence values against the gold validation split; the current values are explicit
   engineering priors, not measured probabilities.
6. Compare the deterministic baseline with a small learned token-classification or span model.
   Retain rules wherever the learned model does not improve held-out accuracy or robustness.
7. Route OCR confidence into extraction confidence rather than treating the extraction score in
   isolation.
8. Add extraction outputs to the synthetic case pipeline only after field/provenance evaluation
   meets documented acceptance thresholds.

## Acceptance principle

A normalized field is useful only when the system can show exactly where it came from and when a
reviewer should distrust it. Coverage should expand only behind a gold benchmark; regex count or
demo output alone does not complete this phase.
