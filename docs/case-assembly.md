# Case assembly — running the Milestone 0 spine on real documents

Milestone 0 (README §23) proved the pipeline spine with hand-authored evidence: classification and
extraction were fixtures, and the point was the *shape* of the flow. Phase 1.5 built the real
classifier and Phase 3 built the real extractor. This step removes the fixtures.

```bash
uv run rxauth-build-dataset
uv run rxauth-train-classifier          # produces the classifier artifact
uv run rxauth-run-case data/cases/PA-CASE-001
```

`rxauth_ai.case_assembly` walks a directory of documents and runs:

```text
ingest -> classify -> extract -> resolve -> Case -> match -> groundedness -> readiness report
```

Every criterion result is now produced by components the project actually ships, and every value in
the report traces back to a character span in a file on disk.

## Acceptance result

The assembled run reproduces the Milestone 0 criterion profile exactly:

| Criterion | Result | Produced by |
|---|---|---|
| C1 documented diagnosis | SATISFIED | `Diagnosis: Example Condition` in the PA request |
| C2 ≥ 12 weeks of Drug A | SATISFIED | 16 weeks, read from the medication history |
| C3 inadequate response | SATISFIED | duration **and** outcome linked across two lines |
| C4 A1c below 8.0 | SATISFIED | `A1c: 7.4%` in the lab report |
| C5 screening documentation | MISSING | no matching evidence anywhere in the packet |
| C6 therapy duration documented | AMBIGUOUS | “on therapy for several months” — routed, not guessed |

That equivalence is the acceptance test (`tests/test_case_assembly.py`), not a demo: swapping
fixtures for real components must not change what the reviewer is told about the case.

C3 is the interesting one. The packet states the duration on one line and the outcome on the next,
so it is only satisfiable because Phase 3.5 links the two spans into one fact — and the evaluation
cites both spans, not just the anchor.

## What stays a fixture, and why

Two things are still supplied rather than derived, because they belong to later phases:

- **The policy.** README §10 replaces the `PA-104` fixture with retrieval over real public payer
  documents. `resolve_policy` raises on an unknown `policy_id` rather than silently evaluating a
  packet against the wrong requirements.
- **`pa_required`.** README §3 requires the PA trigger to come from a synthetic benefit trigger or
  explicit user input, never from policy text — a public policy cannot establish a live member's
  benefit status. It is read from the case manifest as declared input.

## Case packet format

A packet is a directory containing `case.json` plus any number of ingestable documents
(`.txt`, `.md`, `.pdf`, and the image formats the ingestion layer handles).

```json
{
  "case_id": "PA-CASE-001",
  "patient_synthetic_id": "SYNTH-0001",
  "payer": "Example Health Plan",
  "plan": "Example PPO",
  "medication": "Drug A",
  "indication": "Example Condition",
  "pa_required": true,
  "policy_id": "PA-104"
}
```

Documents are assigned IDs `D1…Dn` in filename order, and extraction scopes evidence IDs to their
document, so every evidence ID in an assembled case is unique and stable across runs.
`run_pipeline` rejects a packet whose payer, medication, or indication disagrees with the policy.

After document-scoped extraction, exact normalized facts repeated in different files receive an
`EvidenceLink`. The diagnosis in the PA request and clinical note, for example, remains two evidence
records joined by one link that lists both document IDs and both spans. Partial facts are never
completed across documents.

`data/cases/PA-CASE-001/` is the checked-in packet. Like everything else in `data/`, every patient,
provider, payer, and drug in it is a fabricated placeholder (README §3).

## Injectable classification

`assemble_case` takes any object satisfying `DocumentClassifierLike` — one method,
`classify_path(path, *, document_id) -> (Document, requires_review)`. The trained baseline is
loaded from `artifacts/classifier_baseline.pkl`, which is a build output and is not committed;
`load_classifier` raises with the two commands that produce it rather than failing obscurely.

The protocol is also what lets the test suite run without a build artifact, and it is where a
served model or the Phase 2 transformer would drop in.

## What the report adds

`CaseReadinessReport` now carries what a reviewer still has to look at, alongside the criterion
tallies:

- `documents_requiring_classification_review` — documents the classifier was not confident about;
- `evidence_total` and `evidence_requiring_review` — extracted fields, and how many produced an
  `ExtractionIssue`.

A case can be "4 of 6 criteria supported" and still need a human on the fields underneath. Reporting
only the criterion tally would hide that.

The JSON artifact (`reports/case_<case_id>.json`) is self-describing: the readiness report, the
classified documents, every evidence record with all of its cited spans, the review issues with
their kinds, spans suppressed during overlap resolution, and cross-document evidence links.

## Next

This closes the last integration item in README §9. §10 replaces `resolve_policy` with payer-policy
retrieval, at which point the policy stops being a fixture too.
