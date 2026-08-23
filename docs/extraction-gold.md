# Extraction gold dataset card

`data/extraction_gold.jsonl` is the first field-level evaluation set for README §9. It is fully
synthetic and hand-authored separately from the extraction rules. Its purpose is to validate the
evaluation and provenance contracts, not to claim production or clinical generalization.

## Schema

Each JSONL row contains:

- `document_id` — unique stable identifier;
- `split` — `validation` or `test`;
- `filename` — synthetic source filename;
- `text` — one page of synthetic document text;
- `expected` — zero or more normalized fields.

Each expected field contains `evidence_type`, optional normalized medication/text/numeric
value/unit/outcome, exact `source_text`, and `requires_review`. Gold character offsets are derived
by locating that hand-authored source string; loading fails unless it occurs exactly once.

## Coverage

The 45 records include:

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
- a negated diagnosis and numeric-result distractors.

There are 25 validation and 20 test documents. All names, medications, conditions, and records are
fabricated placeholders; no PHI is present.

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

## Versioning rules

- Never silently edit expected labels to make a rule pass.
- Move any test record that directly drives implementation into validation and document why.
- Add new test records when exposed records move, retaining stable IDs for existing examples.
- Review additions independently from the rule implementation where possible.
- Record dataset version/hash in future benchmark reports once the corpus begins changing often.
- Do not publish aggregate scores without the dataset size and synthetic/in-distribution caveat.
