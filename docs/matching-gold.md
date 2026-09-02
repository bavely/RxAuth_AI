# Matching gold dataset card

`data/matching_gold.jsonl` is the evaluation set for criteria-to-evidence matching (README §12).
It is fully synthetic and hand-authored. Its purpose is to validate the matching contract —
retrieval, normalization, the five-state result, and the citation attached to each result — not to
claim production or clinical generalization.

## Schema

Each JSONL row contains:

- `match_id` — unique stable identifier;
- `split` — `validation`, `test`, or `challenge`;
- `medication` / `indication` — case-level context; `indication` is what a `diagnosis` criterion is
  checked against;
- `criterion` — one structured requirement: `criterion_type`, optional `medication`, `operator`,
  `expected_value`, `unit`, `required_outcome`, extraction `confidence`, and its `source_text`;
- `evidence` — zero or more normalized facts, each with an `id`, `evidence_type`, optional
  medication/text/numeric value/unit/outcome, extraction `confidence`, and its `source_text`;
- `expected_result` — one of `SATISFIED`, `NOT_SATISFIED`, `MISSING`, `AMBIGUOUS`,
  `HUMAN_REVIEW_REQUIRED`;
- `expected_evidence_ids` — the exact evidence the evaluation must cite as support;
- `note` — why the record exists. Required on every record.

The benchmark builds a `Case` and a `Criterion` from each row, so evidence provenance is derived:
the document ID is the segment of the evidence ID before the first hyphen, and the character span
is the full length of `source_text`. Multi-document cases are written by giving evidence IDs
different prefixes (`D1-EV1`, `D2-EV1`).

## What is scored

A record counts as correct only when **both** the five-state result and the exact set of cited
evidence IDs agree. The right status with the wrong source is a failure, because a criterion result
a reviewer cannot trace is not a usable answer.

The report also breaks out `false_support_rate` — how often a `SATISFIED` result was returned for a
record that should not have been satisfied. It is reported separately because an unsupported
`SATISFIED` is the most dangerous error this component can make: every other mistake asks a human
for more work, and this one tells them there is none.

`retrieval_recall` is measured against `candidate_evidence_ids`, so a record can show that the right
fact was retrieved and then wrongly discarded during evaluation.

Macro F1 averages across all five results, so **every split contains all five**. A split missing a
state would be averaged against a class it never asked about, and would not be comparable to the
others. `tests/test_benchmark_matching.py` enforces this.

## Coverage

There are 15 validation, 14 test, and 13 challenge records (42 total).

**Retrieval and relevance**

- exact type and medication match; wrong evidence type; a different medication;
- brand-to-generic normalization in both directions (policy says `Humira`, chart says
  `adalimumab`, and the reverse);
- a biosimilar (`adalimumab-atto`) that shares a prefix with the reference product and must
  *not* normalize to it;
- an unattributed duration with no medication, which must not be assumed to belong to the drug
  the policy names;
- a diagnosis matching the case indication, a diagnosis of a different condition, a case-insensitive
  match that must succeed, and `"Example Condition, suspected"` — a substring near-miss that must
  fail, because a documented diagnosis is a stronger claim than a suspected one.

**Normalization and comparison**

- `>=`, `<=`, and `==` comparators;
- the inclusive boundary (exactly 12 against "at least 12");
- exact seven-day conversion at 84 days (12 weeks), 83 days (just under), and 85 days (just over);
- calendar months refused in both directions — weeks against a month threshold and months against a
  week threshold — because no exact conversion exists and the threshold can sit between the
  plausible readings;
- a bare number with no unit against a criterion that states one;
- a criterion with no unit constraint, where there is nothing to convert.

**Uncertainty routing**

- vague duration language with no number, which stays `AMBIGUOUS` rather than being guessed or
  reported `MISSING`;
- a required outcome the evidence is silent about — silence is not evidence of the required outcome;
- evidence below the matching confidence threshold, and evidence exactly at it (the threshold is a
  floor, not an exclusive bound);
- a criterion whose own extraction confidence is too low, which routes before retrieval and cites
  nothing;
- an `unstructured` requirement, which routes to review rather than being dropped or scored
  `MISSING`;
- an empty record, which is `MISSING` — a request for a document, not a denial.

**Aggregation across several facts**

- two supporting facts, both cited, so corroboration is not hidden behind the most confident span;
- a direct contradiction, cited on both sides — twice, once with the satisfying fact more confident
  and once with the failing fact more confident, so deferring to confidence cannot resolve it
  silently;
- every relevant fact failing, with all of them cited;
- a satisfying fact alongside an unreadable one, where only the satisfying fact is cited as support;
- two vague facts, which do not add up to one precise fact;
- a vague fact alongside a failing one, which stays `AMBIGUOUS` rather than denying the case on an
  incomplete reading;
- a three-way case where a contradiction and a vague statement coexist, and the two contradicting
  spans are the ones put in front of the reviewer.

All names, medications, and conditions are fabricated or public medication names. No PHI is present.

## Development history

The gold set was authored after `evidence-match-v2` was written, by working from the documented
matching contract rather than from observed output, and each expectation was traced through the
contract before the benchmark was run. All 40 initial records passed on the first run.

That first run reported macro F1 of `0.800` on validation and challenge. This was a defect in the
gold set, not the matcher: validation contained no `AMBIGUOUS` record and challenge contained no
`NOT_SATISFIED` record, so each split was averaged against a class it never exercised. `MATCH-V15`
and `MATCH-C13` were added to close the gap, and the constraint is now enforced by a test rather
than left as a convention.

**This is a regression harness and a contract check, not an independent audit.** The author of these
records had read the implementation. A gold set written that way reliably catches future
regressions and reliably fails to discover behaviours nobody thought to question. Two candidates
for external review are recorded here rather than silently accepted:

- `MATCH-C02` pins the current asymmetry in aggregation. A *readable* fact that contradicts a
  satisfying one routes the criterion to `HUMAN_REVIEW_REQUIRED`, while an *unreadable* fact
  alongside a satisfying one leaves the result `SATISFIED` and drops the unreadable span from the
  citation list. The unreadable span stays visible in `candidate_evidence_ids` and in the readiness
  report's extraction-issue count, so it is disclosed — but it is disclosed somewhere else. Whether
  that asymmetry is right is a design question for review, not a bug.
- `MATCH-V10` and `MATCH-C11` use the `exists` operator. `exists` is not in the matcher's operator
  table, so any `expected_value` set alongside it is silently ignored. No current criterion
  extractor emits that combination; nothing guards against one that does.

## Versioning rules

- Never silently edit an expected result to make the matcher pass. If the matcher and the gold
  disagree, decide which is wrong in the open and record the decision here.
- Every record carries a `note` explaining why it exists; a record that cannot be justified in one
  sentence does not belong in the set.
- Move any test record that directly drives an implementation change into validation and say why.
- Keep all five results represented in all three splits.
- Do not publish aggregate scores without the dataset size and the synthetic/in-distribution caveat.
