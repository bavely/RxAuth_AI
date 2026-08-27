# Phase 3 — information extraction with confidence

Phase 3 converts ingested document text into normalized `Evidence` records without losing the
source needed to audit each value. The selected runtime is deliberately deterministic; a small
learned span model was implemented and measured, then rejected because it did not improve held-out
accuracy or provide the complete normalization, provenance, and review contract.

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

Medication recognition is explicit rather than open-ended. Synthetic `Drug A`–`Drug Z` names keep
their display form. Supported brand/generic aliases normalize to a lower-case generic name. The
lexicon is local, versioned, and auditable; unknown names are not guessed.

Each `Evidence` item records:

- a deterministic document-scoped evidence ID;
- normalized evidence type, text/numeric value, medication, unit, and outcome where present;
- extraction confidence, the name of the rule that produced the anchor span, and the
  `regex-v3` extractor version;
- document ID, filename, page, exact source text, and inclusive/exclusive page-character
  offsets — for the anchor span and for every additional span cited for the same fact;
- the ingestion confidence of the weakest cited page, so an OCR score is visible next to the
  value it produced.

## The resolution pipeline

Matching patterns is only the first stage. Raw matches pass through four deterministic stages
before they become `Evidence`:

```text
match rules per page
  -> resolve_overlaps         one span wins when two rules claim the same text
  -> merge_repeated_mentions  one fact, several citations
  -> link_previous_therapy    duration + outcome combined only when unambiguous
  -> combine_confidence       page ingestion confidence folded into field confidence
```

**Overlap precedence.** When two rules claim overlapping text for the same evidence type, the
longer span wins — it consumed more of the sentence and therefore encoded more context — and
declaration order in `_RULES` breaks a tie. The losing match is not dropped silently: it is
returned as a `SuppressedSpan` naming the rule that superseded it, because a suppressed match and
a rule that never fired are otherwise indistinguishable. Overlaps *between different* evidence
types are left alone: a date inside a therapy line is genuinely two facts about the same words.

**Repeated mentions.** A payer named on a card heading and again on a coverage line is one fact
with two citations, not two facts. Facts whose normalized fields are identical collapse into one
record; the strongest span (then the earliest) becomes the anchor and the rest become supporting
provenance, so every mention stays citable. Facts differing in *any* normalized field never
merge — two therapy durations for the same drug stay two facts.

**Multi-span linking.** The `Evidence` contract now cites several spans for one fact, which is
what makes it possible to combine a therapy duration stated on one line with the outcome stated on
the next. The link is made only when the document leaves no choice: exactly one duration-only span
and exactly one outcome-only span for that medication. Two durations, two outcomes, or a different
medication all leave the spans unlinked, and the outcome spans are flagged `AMBIGUOUS_LINKAGE` —
the extractor will not choose a pairing on the reviewer's behalf.

**Source-aware confidence.** Field confidence is multiplied by the ingestion confidence of the
page it was read from. Digital text and text PDFs ingest at 1.0, so this changes nothing for them;
a field read off a poor scan is no longer presented as confidently as the same field read off
clean text. The independence assumption behind the product is an explicit engineering prior — the
gold set is digital text only, so there is nothing yet to fit it against.

## Review routing

An extraction that needs a human is retained with its source and additionally produces an
`ExtractionIssue`. The three reasons are genuinely different and are no longer collapsed into one
number:

| Kind | Meaning |
|---|---|
| `low_confidence` | The span may have been misread. Threshold-controlled. |
| `incomplete_value` | The span was read correctly and does not state enough for a deterministic check. |
| `ambiguous_linkage` | The document supports several equally plausible readings; no pairing was chosen. |

Only `low_confidence` moves with `--confidence-threshold`. Lowering the threshold to zero does not
silence an incomplete value, because the threshold is not what is wrong with it.

Run the extractor with:

```bash
uv run rxauth-extract data/documents/clinical_note/doc_0002.txt --document-id SYN-EXAMPLE
```

The CLI returns JSON containing all evidence, review issues, suppressed spans, and a top-level
`requires_human_review` flag.

## Gold benchmark

```bash
uv run rxauth-benchmark-extraction
```

It reads `data/extraction_gold.jsonl` and writes `reports/extraction_benchmark.md`. The 61-document
corpus has 29 validation, 20 test, and 12 challenge documents. Legacy one-page rows and typed
multi-page rows share one loader; challenge pages can declare text/PDF/OCR methods and ingestion
confidence. The benchmark reports:

- document-scoped exact field precision, recall, and F1;
- normalized-value accuracy for fields aligned by type and source;
- exact page/character provenance-span accuracy;
- evidence-level review precision/recall/F1 and document review accuracy;
- extraction latency and concrete false-positive/false-negative details.

`regex-v3` scores 1.000 across these metrics on validation, test, and challenge. This means the
current rules satisfy the small, explicit synthetic contract; it does not demonstrate robustness
to real clinical writing, broader OCR variation, unseen terminology, or independent authorship. See
[the extraction dataset card](extraction-gold.md) for coverage and history. The four resolution
records (`GOLD-046` through `GOLD-049`) were authored and shown to fail under `regex-v1` before the
resolution stages were written; the test split was not touched and still passes unchanged.

## Confidence calibration

```bash
uv run rxauth-calibrate-extraction
```

It reads the **validation split only** and writes `reports/extraction_calibration.md`: observed
accuracy per assigned confidence value, accuracy per rule, expected calibration error, Brier
score, and a review-threshold sweep. The test split never tunes a confidence value or a threshold.

The measurement deliberately stops at reporting. Every bucket's gap is positive — the rules are
correct more often than they claim on this corpus — but each bucket holds a handful of fields drawn
from documents written in the vocabulary the rules target, so fitting a mapping to them would
encode the sample rather than calibrate the extractor. The confidence values remain documented
priors until the calibration split contains independently authored paraphrases and OCR-degraded
pages.

The sweep's useful reading is the *width* of the threshold band that reproduces gold routing, not
the best value inside it. A threshold that had to be tuned precisely to work would be a warning
sign, not a result.

The most useful finding is structural: fields in the lowest confidence bucket are correct and still
need a reviewer. One number cannot mean both "may have been misread" and "read correctly, says too
little", which is what motivated the three issue kinds above.

The challenge slice now contains OCR-degraded labels and source-confidence routing, but it is still
locally authored. That improves regression coverage without satisfying the stronger independent
validation requirement.

## Learned-model comparison

```bash
uv run rxauth-compare-extractors
```

`token-logreg-v1` is a reproducible token-level multinomial logistic-regression span model. It fits
20 fixed development documents (seed 42), uses the remaining nine validation records for the
selection decision, then reports exact evidence type/page/start/end spans on untouched test and
challenge. It scores 0.084 exact-span F1 on test and 0.000 on challenge; `regex-v3`
scores 1.000 on both. The candidate also lacks normalized values, overlap suppression, multi-span
facts, and typed review issues, so `regex-v3` remains selected. See
`reports/extraction_learned_comparison.md`.

## Cross-document corroboration

Case assembly creates an `EvidenceLink` when two or more documents state the exact same normalized
fact. The original `Evidence` records remain separate, and the link lists every evidence ID,
document ID, and provenance span. Partial facts are never completed across documents: a duration in
one file and an outcome in another remain two facts for human review.

## Design boundaries

- Extraction consumes `IngestedDocument`; it does not duplicate text/PDF/OCR handling.
- Character offsets are page-relative and use Python's inclusive-start/exclusive-end convention.
- Spans are combined into one fact only within a single document. Exact facts across documents stay
  separate and receive an `EvidenceLink` that preserves every document-specific citation.
- No LLM or external clinical terminology service is used. The learned comparison is an offline
  experiment and is not the selected extraction runtime.
- Patterns intentionally cover only explicit synthetic forms and make no production-validity
  claim.

## §9 completion checklist

- ~~Extend the evidence contract to support multiple provenance spans, then combine duration and
  outcome statements only when both cited spans refer to the same medication/document context.~~
- ~~Define precedence and deduplication when multiple patterns identify overlapping spans.~~
- ~~Calibrate confidence values against the gold validation split.~~
- ~~Route OCR confidence into extraction confidence rather than treating the extraction score in
  isolation.~~
- ~~Add extraction outputs to the synthetic case pipeline.~~ See
  [the case-assembly guide](case-assembly.md).
- ~~Expand medication recognition beyond `Drug A`–`Drug Z` and define explicit normalization.~~
- ~~Add OCR corruption, multi-page cases, negation, paraphrases, and harder cross-class distractors
  to a separately reported challenge split.~~
- ~~Compare the deterministic baseline with a small learned token-classification model and retain
  rules unless learning improves held-out robustness.~~
- ~~Link exact facts across documents without losing per-document attribution or completing partial
  facts across files.~~

Externally authored, independently reviewed data is still required before treating any score as a
generalization result. That is an evaluation-hardening requirement for §15, not a claim made by the
completed §9 prototype.

## Acceptance principle

A normalized field is useful only when the system can show exactly where it came from and when a
reviewer should distrust it. Coverage should expand only behind a gold benchmark; regex count or
demo output alone does not complete this phase.
