# Phase 4 — payer-policy retrieval and criteria extraction

Phase 4 removes the last fixture on the policy side. Until now the requirements a case was
judged against came from `synthetic_case.build_policy()` — six hand-written `Criterion` objects.
They are now retrieved from a policy document and read out of its prose.

```bash
uv run rxauth-search-policy "What A1c threshold applies?" \
    --payer "Example Health Plan" --medication "Drug A" \
    --indication "Example Condition" --as-of-date 2026-01-14
uv run rxauth-extract-criteria PA-104:2026-01
uv run rxauth-benchmark-retrieval
uv run rxauth-benchmark-criteria
```

The pipeline README §10 describes runs in two modules:

```text
parse -> clean -> section-detect -> chunk -> attach metadata   (policy_corpus)
  -> embed -> metadata filter -> similarity rank               (policy_retrieval)
  -> structure requirements                                    (criteria_extraction)
```

## Acceptance result

The criteria read out of `PA-104 v2026-01` are structurally identical to the Milestone 0 fixture:

| Criterion | Policy sentence | Structured as |
|---|---|---|
| C1 | "The patient has a documented diagnosis of Example Condition." | `diagnosis`, `exists` |
| C2 | "…completed at least 12 weeks of therapy with Drug A." | `previous_therapy`, `Drug A`, `>= 12 weeks` |
| C3 | "…an inadequate response to therapy with Drug A after at least 12 weeks." | `previous_therapy`, `Drug A`, `>= 12 weeks`, outcome `inadequate_response` |
| C4 | "The most recent A1c is below 8.0 percent." | `lab_a1c`, `< 8.0 percent` |
| C5 | "Required screening documentation is on file." | `screening_doc`, `exists` |
| C6 | "The duration of the most recent therapy course is documented and is at least 8 weeks." | `therapy_duration`, `>= 8 weeks` |

That equivalence is an acceptance test (`tests/test_case_assembly.py`), the same way real
extraction had to reproduce the hand-authored evidence: reading requirements out of policy prose
must not change what the policy requires. `rxauth-run-case data/cases/PA-CASE-001` still reports
4 supported, 1 missing, 1 ambiguous — now with the policy retrieved rather than supplied.

## Retrieval: filter first, rank second

README §10 asks for "metadata filtering + semantic similarity, not vector search alone." The two
stages do different jobs and are kept apart:

- **The metadata filter selects the policy.** Payer, normalized medication, indication, and the
  version window are declared facts, matched exactly.
- **Similarity ranks the passages.** Within the surviving policy, the vector space orders chunks
  so the citations a reviewer sees are the relevant ones.

A filter that excludes everything returns nothing. There is no fallback to unfiltered search,
because *some* policy is not a safe answer to *which policy applies*.

### The corpus is built to punish the alternative

`data/policies/` holds eight synthetic public-style policy versions, and the awkward cases are
deliberate:

| Policy version | Why it is there |
|---|---|
| `PA-104:2026-01` | The one the prototype case resolves to. |
| `PA-104:2024-06` | A superseded version of the *same* policy requiring 8 weeks instead of 12. |
| `PA-207:2025-09` | Same drug, same indication, **different payer**, near-identical wording. |
| `PA-118:2025-03` | Same payer and indication, different drug — a payer-only filter is not enough. |
| `PA-233:2025-07` | Names the generic where a case might name the brand. |
| `PA-341:2026-02` | Not yet effective on the case date, and joins its criteria with **ANY**. |
| `PA-402:2025-11` | Contains a requirement no deterministic rule can structure. |
| `PA-509:2025-05` | Same indication as PA-233 under another payer with a different step agent. |

### Measured ablation

`reports/policy_retrieval.md` runs two embeddings × two modes over 16 gold queries. Both arms of
the ablation receive the same enriched query text and the same embedding; the only variable is
whether the metadata filter runs. Handing the vector-only arm less information would rig the
comparison.

| Embedding | Mode | Correct-policy rate | Recall@5 | MRR | Abstentions correct |
|---|---|---:|---:|---:|---:|
| `tfidf-v1` | metadata+similarity | 1.000 | 0.846 | 0.572 | 3/3 |
| `tfidf-v1` | similarity_only | 0.625 | 0.692 | 0.454 | 0/3 |
| `tfidf-lsa-v1[64]` | metadata+similarity | 1.000 | 0.846 | 0.572 | 3/3 |
| `tfidf-lsa-v1[64]` | similarity_only | 0.625 | 0.692 | 0.454 | 0/3 |

The similarity-only failures are the instructive part. It answers every query that should have
been declined, and it reaches for the *superseded* version of the right policy — both are
properties of the metadata, not of the prose, so no amount of ranking recovers them.

The two embeddings tie. With a few dozen short chunks the truncated SVD preserves the TF-IDF
ranking, so `tfidf-v1` stays the default as the simpler of two indistinguishable options. Neither
is a pretrained semantic model; both are lexical at heart, and the report says so. Paraphrase
robustness is what a dense model would buy, and `EmbeddingBackend` — three methods — is where it
would be bought without touching filtering, ranking, or citation.

### Version selection is driven by an extracted fact

A policy governs a request only inside its version window, so the request date is part of the
lookup. It resolves in this order:

1. `request_date` in the case manifest, when a packet declares it;
2. the `document_date` extracted from the PA request itself — a cited span in a real file;
3. nothing, in which case every version is considered and retrieval **refuses** if more than one
   is in force.

For `PA-CASE-001` it is step 2: `"Date of request: 2026-01-14"` in `01_pa_request.txt`. Picking
`PA-104 v2024-06` instead would silently change C2 from 12 weeks to 8.

### Chunking

README §24 question 5 asks which chunking strategy retrieves exact PA criteria most reliably.
The answer here is `section+enumerated-item`: an enumerated requirement is its own chunk. A
reviewer asking what the policy requires about A1c gets the A1c line and its citation, not the
section that happened to contain it. Every chunk carries policy, version, payer, section, page,
and a character span that still indexes the page it was cut from — asserted in
`tests/test_policy_corpus.py`.

## Criteria extraction: what it refuses to do

The happy path is README §6's worked example: `"at least 12 weeks of Drug A"` becomes
`operator: >=, expected_value: 12, unit: weeks`, and `matching` then evaluates `16 >= 12` in plain
Python. Comparator words map to operators through one auditable table, so "no greater than" is
never read as the "greater than" it contains.

The interesting behaviour is in three refusals.

**A requirement it cannot structure is kept and flagged.** `PA-402` item 3 asks the prescriber for
a written statement about formulary alternatives. No rule turns that into a comparison, so it
becomes a `criterion_type="unstructured"` criterion plus an `UNSTRUCTURED_REQUIREMENT` issue, and
`matching` returns `HUMAN_REVIEW_REQUIRED` rather than `MISSING` — "no evidence found" would be a
false statement about the case when the truth is about the rule set. Dropping it instead would
keep criterion F1 at 1.000 while shrinking the policy the case is judged against, and the case
would read as readier than it is. That is why the benchmark scores unstructured recall separately.

**An exclusion is not a criterion.** "Not covered when ANY of the following applies" reads almost
exactly like a coverage list. Exclusions are parsed from their own section, marked
`polarity="exclusion"`, kept out of the conjunctive criteria list, and counted in the readiness
report as `policy_exclusions_not_evaluated`. The deterministic matcher has no NOT semantics;
reporting a reason to *deny* coverage as a satisfied requirement would invert the answer.

**ALL is not ANY.** The connective is read from the section lead-in. `PA-341` joins its criteria
with ANY, so `run_pipeline` refuses it by name rather than scoring a conjunction that would fail a
case the policy actually covers.

### Measured

`reports/criteria_extraction.md` scores 32 gold criteria across all eight policy versions. A
criterion counts as correct only when its type, medication, operator, threshold, unit, required
outcome, **and** quoted source text all agree.

| Metric | Value |
|---|---:|
| Criterion F1 | 1.000 |
| Provenance-span accuracy | 1.000 |
| Connective accuracy | 1.000 |
| Exclusion-count accuracy | 1.000 |
| Unstructured-requirement recall | 1.000 |

The gold loader refuses a partial dataset: every policy version in the corpus must have a gold
record, otherwise a policy the extractor mishandles can go unmeasured.

## What these numbers do and do not establish

The policy corpus is **synthetic public-style text authored locally for this prototype**, in the
forms the rules expect. A perfect score measures the declared contract — normalization,
provenance, version selection, connective detection, and the routing of what could not be
structured — not generalization to real payer publications. Parsing genuinely public payer PDFs,
and evaluating retrieval against externally authored queries, belongs to evaluation hardening
(README §15).

Two guardrails hold regardless of corpus:

- **`pa_required` is still declared input** (README §3). A public policy cannot establish a live
  member's benefit status, so the PA trigger comes from the case manifest and is never inferred
  from policy text.
- **Nothing is answered without a citation.** Every retrieved chunk and every structured criterion
  names its policy, version, page, character span, and exact source text.

## Data and code map

| Path | What it holds |
|---|---|
| `data/policies/` | Eight synthetic public-style policy versions. |
| `data/policy_retrieval_gold.jsonl` | 16 gold queries, 3 of them expecting no policy at all. |
| `data/policy_criteria_gold.jsonl` | Hand-authored structured criteria for every policy version. |
| `src/rxauth_ai/policy_corpus.py` | Parse, clean, section-detect, chunk, attach metadata. |
| `src/rxauth_ai/policy_retrieval.py` | Embedding backends, index, metadata filter, ranking. |
| `src/rxauth_ai/criteria_extraction.py` | Prose to structured `Criterion`, plus what it refuses. |
| `src/rxauth_ai/benchmark_retrieval.py` | Recall@K, Precision@K, MRR, correct-policy rate, ablation. |
| `src/rxauth_ai/benchmark_criteria.py` | Criterion F1, provenance, connective, unstructured recall. |

## Next

README §12 replaces the typed evidence lookup in `matching._find_evidence` with retrieval over the
patient evidence store, and adds model-assisted interpretation for the `AMBIGUOUS` results this
phase still routes to a human.
