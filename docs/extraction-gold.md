# Extraction gold dataset card

`data/extraction_gold.jsonl` is the first field-level evaluation set for README §9. It is fully
synthetic and hand-authored separately from the extraction rules. Its purpose is to validate the
evaluation and provenance contracts, not to claim production or clinical generalization.

## Schema

Each JSONL row contains:

- `document_id` — unique stable identifier;
- `split` — `validation`, `test`, or `challenge`;
- `filename` — synthetic source filename;
- exactly one of `text` (legacy one-page text) or `pages` (typed page number, text, extraction
  method, and ingestion confidence);
- `expected` — zero or more normalized fields.

Each expected field contains `evidence_type`, optional normalized medication/text/numeric
value/unit/outcome, exact `source_text`, optional `page`, and `requires_review`. Gold character
offsets are derived by locating that hand-authored source string on its declared page; loading fails
unless it occurs exactly once there.

## Coverage

The 61 records include:

- `Diagnosis:` and `Assessment:` forms;
- prescriptions;
- prior therapy measured in weeks, months, and days;
- prior-therapy outcomes with and without numeric duration;
- A1c with and without an explicit percent sign;
- patient/member IDs, labeled payer names, and payer-card headings;
- requested days supply, prescription quantity, and three document-date labels;
- LDL cholesterol, ALT, eGFR, and CRP numeric values;
- screening-document presence and a not-attached near miss;
- vague duration language that must route to review;
- multiple fields in one document;
- documents with no extractable evidence;
- a negated diagnosis and numeric-result distractors;
- a therapy duration and its outcome stated on separate lines, which must resolve to one fact
  citing both spans;
- two shapes that must *not* resolve to one fact: several plausible duration/outcome pairings for
  the same medication, and a duration and an outcome belonging to different medications;
- the same payer named twice in one document, which must resolve to one fact with two citations.
- explicit brand/generic medication aliases and an unknown-product near miss;
- alternate diagnosis, prescription, payer, quantity, date, and lab forms;
- a two-page therapy fact whose duration and outcome cite different pages;
- OCR-confused label characters with page confidence that changes review routing;
- harder negation and medication-name distractors.

There are 29 validation, 20 test, and 12 challenge documents. All names, medications, conditions,
and records are fabricated or public medication names; no PHI is present.

## Development history

The first benchmark run exposed one false positive: `No Diagnosis: pending specialist review`
was interpreted as a confirmed diagnosis. Because that record influenced a rule change, it was
promoted from test to validation. A new negative record was added to restore a 12-document test
split. The refreshed test split is useful regression evidence but is not an externally authored,
untouched holdout; the benchmark report and project claims must preserve that limitation.

The next coverage slice was developed gold-first. Fourteen administrative, date, quantity, lab,
and screening fields were added to validation before their rules were implemented. The unchanged
12-document test split remained perfect during development. After the rules were frozen, eight new
test documents (`GOLD-038` through `GOLD-045`) added fresh values, combined-field documents, and
near-miss negatives; the 20-document test split passed on its first run without rule changes. These
records are still authored in-repository and in-distribution, so they strengthen regression coverage
without becoming an independent external holdout.

The four resolution records (`GOLD-046` through `GOLD-049`) were added to validation and confirmed
to fail under `regex-v1` before the resolution stages were implemented: `GOLD-046` produced two
unlinked halves and `GOLD-049` produced a duplicated payer. The two negative records passed
immediately and are regression guards against over-eager linking. The test split was not touched
for this slice and still passes unchanged, so it remains the frozen half of the corpus — with the
caveat, unchanged from above, that it is authored in-repository and is not an independent external
holdout.

The 12 challenge records (`GOLD-050` through `GOLD-061`) add named-medication normalization,
multi-page provenance, OCR-confused labels and confidence, alternate surface forms, negation, and
cross-class distractors. They are reported separately and were not used to fit the learned token
classifier. They are still locally authored and therefore do not constitute an independently
authored or clinical holdout.

## Versioning rules

- Never silently edit expected labels to make a rule pass.
- Move any test record that directly drives implementation into validation and document why.
- Add new test records when exposed records move, retaining stable IDs for existing examples.
- Review additions independently from the rule implementation where possible.
- Record dataset version/hash in future benchmark reports once the corpus begins changing often.
- Do not publish aggregate scores without the dataset size and synthetic/in-distribution caveat.
