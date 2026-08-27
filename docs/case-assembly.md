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
ingest -> classify -> extract -> resolve -> retrieve policy -> extract criteria
   -> Case -> match -> groundedness -> readiness report
```

Phase 4 (README §10–§11, [the Phase 4 guide](phase-4-policy-rag.md)) added the two policy-side
stages. Nothing in the flow is a fixture any more.

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

## Where the policy comes from

The policy is no longer supplied by the packet. `resolve_policy` asks the retrieval index which
policy version governs this payer, drug, indication, and request date, and criteria extraction
reads the requirements out of that version's prose ([Phase 4](phase-4-policy-rag.md)).

The request date driving the version choice is itself an extracted, cited fact: `request_date_for`
prefers the manifest when a packet declares one, and otherwise reads the `document_date` the
extractor found in the PA request (`"Date of request: 2026-01-14"`). Undated, retrieval considers
every version and refuses if more than one is in force rather than defaulting to the newest.

A packet may still name a `policy_id`. That is treated as an assertion to check, never as the
lookup key — if the packet and retrieval disagree, one of them is wrong about the case, and
trusting either silently would hide it.

## What stays declared input, and why

**`pa_required`.** README §3 requires the PA trigger to come from a synthetic benefit trigger or
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

`policy_id` and `request_date` are both optional. `policy_id` asserts which policy the packet
expects retrieval to select; `request_date` overrides the date read off the PA request.

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
  `ExtractionIssue`;
- `criteria_unstructured` — policy requirements that were cited but could not be turned into a
  check;
- `policy_exclusions_not_evaluated` — exclusion rules the policy states that this system does not
  evaluate, disclosed so a reviewer is not told a case is ready against only half of the policy;
- `policy_version` and `policy_effective_date` — which version of the policy produced the answer.

A case can be "4 of 6 criteria supported" and still need a human on the fields underneath. Reporting
only the criterion tally would hide that.

The JSON artifact (`reports/case_<case_id>.json`) is self-describing: the readiness report, the
retrieved policy with its ranked citations and structured criteria, the classified documents, every
evidence record with all of its cited spans, the review issues with their kinds, spans suppressed
during overlap resolution, and cross-document evidence links.

## Next

README §12 replaces the typed evidence lookup in `matching._find_evidence` with retrieval over the
patient evidence store, and adds model-assisted interpretation for the `AMBIGUOUS` results the
pipeline currently routes to a human.
