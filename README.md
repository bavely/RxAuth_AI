# RxAuth AI
## Specialty Pharmacy Prior Authorization Intelligence Copilot

> **Status:** Phase 4 complete. The policy is no longer a fixture: retrieval selects the applicable payer-policy *version* by metadata before it ranks anything, criteria extraction reads that version's requirements out of its prose, and the end-to-end run reproduces the Milestone 0 criterion profile from a document on disk. Criteria-to-evidence matching (§12) is measured against its own gold set, and the AI workflow is complete: the run is an explicit state graph (§13) that drafts a cited requirement checklist (§14) behind a claim-level groundedness gate, with one command scoring every layer against a threshold (§15) and a schema that turns reviewer corrections into regression cases (§16). The reviewer UI and the service layer are next.
> **Goal:** One flagship AI-engineering project that begins as IBM AI Engineering coursework, grows into a portfolio system, and has a credible path to a commercial pilot — built incrementally so the commit history traces the progression from classical ML through deep learning to RAG and agentic systems.

**Author:** Bavely S. Tawfik — [pavli-tawfik.com](https://pavli-tawfik.com) · [linkedin.com/in/bavelytawfik](https://www.linkedin.com/in/bavelytawfik) · [github.com/bavely](https://github.com/bavely)

---

## Quick start

The current implementation is a Python 3.12 prototype with an offline Milestone 0 pipeline, a reproducible classical-ML document classifier, and an optional Phase 2 transformer experiment.

```bash
uv sync --group dev
uv run rxauth-milestone0
uv run rxauth-build-dataset
uv run rxauth-benchmark-ingestion
uv run rxauth-train-classifier
uv run rxauth-extract data/documents/clinical_note/doc_0002.txt --document-id SYN-EXAMPLE
uv run rxauth-benchmark-extraction
uv run rxauth-calibrate-extraction
uv run rxauth-compare-extractors
uv run rxauth-search-policy "What A1c threshold applies?" --payer "Example Health Plan" --medication "Drug A" --indication "Example Condition" --as-of-date 2026-01-14
uv run rxauth-extract-criteria PA-104:2026-01
uv run rxauth-benchmark-retrieval
uv run rxauth-benchmark-criteria
uv run rxauth-benchmark-matching
uv run rxauth-run-case data/cases/PA-CASE-001
uv run rxauth-evaluate
uv run rxauth-check-reports
uv run pytest
```

`rxauth-run-case` is the one that runs the whole spine on real files — ingest, classify, extract,
resolve, retrieve the policy, structure its criteria, match, groundedness gate — so it needs the
classifier artifact that `rxauth-train-classifier` writes.

Run the optional deep-learning comparison separately:

```bash
uv sync --extra deep --group dev
uv run rxauth-train-deep-classifier --seeds 7 42 73
```

The synthetic classifier and rendered ingestion corpora are checked in for reproducibility. See [the Milestone 0 guide](docs/milestone-0.md) for the pipeline spine, [the Phase 1.5 guide](docs/phase-1.5.md) for ingestion and benchmark details, [the Phase 2 guide](docs/phase-2.md) for the transformer protocol, results, and model decision, [the Phase 3 guide](docs/phase-3-extraction.md) for extraction and its resolution stages, [the Phase 4 guide](docs/phase-4-policy-rag.md) for policy retrieval and criteria extraction, and [the case-assembly guide](docs/case-assembly.md) for the end-to-end run on real documents.

### Phase 1.5 outcomes

- Text, text-PDF, and image ingestion share a page-level typed contract.
- Images pass through deterministic grayscale, denoise, deskew, and threshold preprocessing.
- Synthetic PDFs and degraded PNG scans are reproducibly rendered and tracked in `data/ingestion_manifest.csv`.
- Classification uses case- and template-family-isolated train/validation/test/challenge splits.
- The baseline reports macro F1, confidence calibration, review-routing rate, latency, and challenge failures.
- The fitted classifier can be saved, loaded, and used to create typed `Document` predictions.
- OCR text accuracy is intentionally unreported until an OCR runtime is configured; optional Tesseract support and an injectable backend are available.

### Phase 2 outcomes

- The transformer uses the same leakage-resistant train/validation/test/challenge contract as the baseline.
- Checkpoint selection and early stopping use validation macro F1 only; test and challenge data never select the model.
- The paired report compares F1, calibration, review routing, latency, artifact size, and failure cases.
- Three deterministic seeds report mean and sample standard deviation; the saved artifact is selected by validation macro F1 only.
- Transformer mean macro F1 was `0.889 ± 0.042` on test and `0.830 ± 0.060` on challenge, below the baseline's `0.979` and `0.916`.
- The selected transformer artifact was about 256 MiB and 33.7 ms/document on CPU versus 0.09 MiB and 0.004 ms/document for the baseline on the same run.
- The classical TF-IDF + Logistic Regression model remains selected; the transformer did not justify its quality, calibration, routing, latency, or size cost.
- Deep-learning dependencies are optional, so the core package and CI stay lightweight.

### Information extraction build status

- The deterministic extractor recognizes diagnoses, prior-therapy duration/outcome, prescriptions, patient/member IDs, payer names, days supply, prescription quantities, document dates, A1c/LDL/ALT/eGFR/CRP values, screening-document presence, and ambiguous therapy duration.
- Medication extraction uses an explicit, auditable alias lexicon: `Drug A`–`Drug Z` remain stable synthetic placeholders, while supported brand/generic aliases normalize to lower-case generic names. Unknown products are never guessed.
- Every extracted field records document, filename, page, exact source text, inclusive/exclusive character offsets, confidence, the rule that produced it, and extractor version.
- Raw matches pass through four deterministic resolution stages before becoming evidence: overlap precedence, repeated-mention merging, therapy duration/outcome linking, and source-aware confidence.
- One fact may now cite several spans. A duration stated on one line and its outcome on the next become a single complete fact — but only when the document leaves exactly one plausible pairing; anything else stays unlinked and is flagged `ambiguous_linkage`.
- Review routing distinguishes `low_confidence` (the span may have been misread) from `incomplete_value` and `ambiguous_linkage` (the span was read correctly and still needs a human). Only the first moves with the confidence threshold.
- Field confidence is multiplied by the ingesting page's confidence, so a field read off a poor scan is not presented as confidently as the same field read off clean text.
- A 61-document hand-authored JSONL gold set separates 29 development/validation, 20 frozen test, and 12 challenge examples. The challenge contract supports multi-page pages, OCR text and confidence, medication aliases, harder paraphrases, negation, and cross-class distractors.
- `rxauth-benchmark-extraction` reports exact field precision/recall/F1, normalized-value accuracy, provenance-span accuracy, review routing, latency, and concrete failures.
- `regex-v3` scores 1.000 on validation, test, and challenge. This small, locally authored synthetic result validates the declared contract, not production or clinical generalization.
- `rxauth-calibrate-extraction` measures the confidence values against the **validation split only** and writes `reports/extraction_calibration.md`: reliability per assigned confidence, accuracy per rule, expected calibration error, Brier score, and a review-threshold sweep.
- The calibration deliberately stops at reporting. Every bucket is more accurate than it claims, but with a handful of in-distribution fields per bucket, fitting a mapping would encode the sample rather than calibrate the extractor. The values stay documented priors.
- `rxauth-compare-extractors` trains a token-level logistic-regression span model on 20 development records and selects on nine validation records, leaving test and challenge untouched. It scores 0.084 exact-span F1 on test and 0.000 on challenge versus 1.000 for `regex-v3`, so the complete deterministic extractor remains selected.
- Exact normalized facts repeated across documents are linked as corroboration without merging their document-scoped evidence or using partial facts to manufacture a complete statement.
- Externally authored clinical/OCR evaluation remains necessary before any generalization claim; it belongs to evaluation hardening (§15), not the completed §9 prototype deliverable.

### Payer-policy retrieval and criteria extraction

- The policy is no longer a fixture. `rxauth-run-case` retrieves the applicable payer-policy *version* from `data/policies/` and reads its requirements out of the document's prose.
- Retrieval filters on metadata **before** it ranks. Payer, normalized medication, indication, and the version window are matched exactly; similarity then orders passages *inside* the selected policy so the citations a reviewer sees are the relevant ones.
- When the filter excludes everything, retrieval returns nothing. There is no fallback to unfiltered search — "some policy" is not a safe answer to "which policy applies."
- The synthetic corpus is built to punish the alternative: `PA-207` covers the same drug and indication as `PA-104` under a different payer in near-identical wording, and `PA-104` ships in two versions whose prior-therapy thresholds differ (12 weeks vs 8).
- Measured ablation over 16 gold queries, both arms given the same query text and the same embedding: metadata+similarity scores a **1.000** correct-policy rate against **0.625** for similarity alone. The vector-only arm answers all three queries that should have been declined, and reaches for the superseded version of the right policy.
- The two embeddings (`tfidf-v1`, `tfidf-lsa-v1`) score identically on this corpus, so the simpler one stays the default. Both are lexical at heart and neither is presented as a pretrained semantic model; `EmbeddingBackend` is the three-method seam where a dense model or pgvector drops in.
- The policy version is chosen by the request date, and that date is itself an extracted, cited fact — `"Date of request: 2026-01-14"` read off the PA request. Undated, retrieval considers every version and refuses if more than one is in force rather than defaulting to the newest file.
- Chunking is `section+enumerated-item`: an enumerated requirement is its own chunk, so a question about A1c returns the A1c line and its citation, not the section around it. Every chunk carries policy, version, payer, section, page, and a character span that still indexes the page it was cut from.
- Criteria extraction turns prose into the structured rule the matcher evaluates — `"at least 12 weeks of Drug A"` becomes `operator: >=, expected_value: 12, unit: weeks` — with comparator words mapped through one auditable table, so "no greater than" is never read as the "greater than" it contains.
- A requirement no rule can structure is **kept and flagged**, never dropped, and routes to `HUMAN_REVIEW_REQUIRED` rather than `MISSING`. Dropping it would hold criterion F1 at 1.000 while shrinking the policy the case is judged against, and the case would read as readier than it is.
- Exclusions are parsed from their own section, marked `polarity="exclusion"`, kept out of the conjunctive criteria list, and counted in the readiness report. The deterministic matcher has no NOT semantics, and reporting a reason to deny coverage as a satisfied requirement would invert the answer.
- A policy that joins its criteria with ANY is refused by name rather than scored as a conjunction that would fail a case the policy actually covers.
- `rxauth-benchmark-criteria` scores 32 gold criteria across all eight policy versions at **1.000** F1, provenance-span accuracy, connective accuracy, and unstructured-requirement recall. A criterion counts as correct only when its type, medication, operator, threshold, unit, required outcome, and quoted source text all agree.
- The criteria read out of `PA-104 v2026-01` are structurally identical to the Milestone 0 fixture. That equivalence is the acceptance test for this phase, not a demo.
- The policy corpus is synthetic public-style text authored locally in the forms the rules expect. These scores validate the declared contract — normalization, provenance, version selection, connective detection, and the routing of what could not be structured — not generalization to real payer publications.

### Criteria-to-evidence matching

- `evidence-match-v2` retrieves every structurally relevant fact before it decides anything, and reports the candidates it considered alongside the ones it cited, so a fact that was found and then discarded is visible rather than invisible.
- Evidence type and named medication are hard constraints. A `diagnosis` must equal the case indication: `"Example Condition, suspected"` does not satisfy a rule requiring a documented diagnosis of `"Example Condition"`, because substring matching would erase the difference between suspected and documented.
- Medication comparison runs through the same auditable lexicon as extraction, in both directions — the policy may say `Humira` while the chart says `adalimumab`. A biosimilar outside the lexicon (`adalimumab-atto`) shares a prefix with the reference product and is **not** normalized into it.
- Days and weeks convert exactly. Calendar months do not convert in either direction, because a month has no exact week count and the threshold can sit between the plausible readings; the case routes to a reviewer instead of being resolved by a rounding rule.
- Every supporting fact is cited, not just the most confident one. Contradictory facts route to `HUMAN_REVIEW_REQUIRED` with **both** spans attached — and deferring to the more confident span is exactly the shortcut the challenge split is built to catch.
- Ambiguity outranks failure. A vague statement alongside a failing one stays `AMBIGUOUS` rather than reporting `NOT_SATISFIED`, because denying a case on an incomplete reading is a worse error than asking for a better document.
- The model-assisted stage is a seam, not a dependency. `AmbiguityInterpreter` accepts only a typed decision, rejects any interpreter that tries to return `MISSING`, and routes a low-confidence interpretation to review. The offline default abstains, so no published number here contains a model-generated value.
- `rxauth-benchmark-matching` scores 42 hand-authored matches — 15 validation, 14 test, 13 challenge — at **1.000** result accuracy, evidence F1, retrieval recall, and citation accuracy, with a **0.000** false-support rate. A record counts as correct only when the five-state result *and* the exact set of cited evidence IDs both agree: the right status with the wrong source is a failure.
- False-support rate is reported separately because an unsupported `SATISFIED` is the most dangerous error this component can make. Every other mistake asks a human for more work; that one tells them there is none.
- All five results appear in all three splits, enforced by a test. A split missing a state would be averaged against a class it never exercised and would not be comparable to the others.
- The gold set was authored from the documented contract rather than from observed output, but by someone who had read the implementation. It is a regression harness and a contract check, not an independent audit, and [the gold dataset card](docs/matching-gold.md) records two behaviours it pins without endorsing.
- `rxauth-check-reports` fails CI when a committed report stops reproducing. Wall-clock timings are excluded — they vary by more than 2x between runs, and a gate that fails on every commit is a gate somebody removes — while every quality metric, count, failure table, and citation ID is compared exactly.

### End-to-end on real documents

- `rxauth-run-case data/cases/PA-CASE-001` runs a document packet through ingest → classify → extract → resolve → retrieve policy → extract criteria → match → groundedness gate, replacing every one of Milestone 0's hand-supplied inputs with the components the project actually ships.
- The assembled run reproduces the Milestone 0 criterion profile exactly (4 supported, 1 missing, 1 ambiguous). That equivalence is an acceptance test, not a demo.
- Two of those criteria are only satisfiable because the therapy duration and its outcome were linked across two lines — and the evaluation cites both spans, not just the anchor.
- The readiness report now also counts what a reviewer still has to look at: document classifications below threshold, extracted fields that produced an issue, policy requirements that could not be structured, and exclusion rules the system does not evaluate. A case can be "4 of 6 supported" and still need a human underneath.
- Classification is injectable behind a one-method protocol, so the trained baseline, a served model, or a test stub all drop in without touching assembly.
- `pa_required` stays declared input: §3 forbids inferring a live benefit from policy text, because a public policy cannot establish a member's benefit status. It is the only remaining supplied value in the flow. See [the case-assembly guide](docs/case-assembly.md).

### The workflow, the draft, and how both are checked

- The end-to-end run is an explicit state graph of thirteen named nodes over typed state (§13). Every node records how it ended, and a failure marks the nodes after it `not_run` — so a partial result can never be read as a complete one.
- Each node records the component versions its output depended on: extractor, embedding, criteria extractor, matcher, normalization, generator. "Which matcher produced this evidence" is answerable from the report rather than from the commit that wrote it.
- The graph is linear on purpose (§13), and the executor is hand-written rather than LangGraph. That is a considered trade recorded in [ADR 001](docs/adr-001-workflow-runtime.md): this graph makes zero model calls and has no branches, loops, or concurrency, so the framework's capabilities would go unexercised while its dependency tree landed in a package whose CI lightness is a stated goal. The decomposition and typed contracts are runtime-agnostic, so adopting LangGraph later is an adapter, not a rewrite.
- `Node.retries` exists and every node sets it to zero, because every node is deterministic and offline and a retry could only repeat the same failure. A test asserts that, so the first networked node has to justify its own policy.
- Building the graph exposed that every document was being read twice — once by the classifier and once by the extractor, meaning every scan was OCR'd twice. Ingestion is now one node feeding both.
- `rxauth-run-case` drafts a **cited requirement checklist** (§14). Sentences are assembled from structured results and quote their source spans verbatim; a missing requirement drafts as "not documented", never as a guess.
- The claim-level groundedness gate re-derives support from the case rather than trusting the generator's own citations. Every number and every medication name in a drafted sentence must appear in a span that sentence cites, so a generator that invents a duration **and** a citation for it still fails. A restated policy threshold is allowed, because quoting the requirement is not a claim about the patient.
- `DraftGenerator` is the seam a prompted model drops into, and it inherits no extra trust: a test swaps in a deliberately fabricating generator and asserts the gate catches it. The offline default is deterministic, which is why no published number here contains model-generated text.
- No field anywhere marks a checklist submittable. §20 puts autonomous submission permanently out of scope, so the data model gives it nowhere to be recorded, and the terminal node reports what a person still has to do.
- `rxauth-evaluate` (§15) scores **22 metrics across six layers** — classification, extraction, retrieval, criteria, matching, generation — against thresholds set at the values the current code produces. Regressions fail the build; relaxing a threshold is a visible diff. False-support rate and unsupported-claim rate are the two ceilings, because every other error asks a human for more work and those two tell them there is none.
- Reviewer corrections are typed, append-only, and versioned (§16). Agreement is stored too — a component nobody corrects and one nobody reviews are otherwise indistinguishable — which is what makes correction-rate-per-matcher-version answerable.
- A correction exports as a `matching_gold.jsonl` record and is verified against the real loader in a test, so a reviewer disagreeing with the matcher becomes a regression case rather than an anecdote. Exports default to the validation split, keeping the frozen test split frozen.

### Configuration, logging, and model artifacts

- Every path, threshold, and logging choice comes from one validated `Settings` object read from `RXAUTH_*` environment variables. An empty environment reproduces the historical CLI defaults exactly — which is how this landed without moving a single number in `reports/`.
- There are no CWD-relative `Path()` literals left outside `config.py`. A CLI flag beats an environment variable beats the default, and the settings object is the *source* of the argparse defaults rather than a competitor to them.
- Settings are frozen and read once. Settings that change under a running process produce bugs nobody can reproduce.
- Structured logging implements the §18 schema — `request_id`, `case_id`, `workflow_node`, versions, `latency_ms`, `estimated_cost_usd`, `error_type` — with one line per workflow node, correlated by a request ID. Before this the project had **zero** `logging` calls and 100+ `print` statements.
- Log fields are **allow-listed, not deny-listed**. `log_event` takes an event name and structured fields — never a format string — and anything outside `LOGGABLE_FIELDS` is dropped with the drop itself recorded. A deny-list of "PHI-ish" names fails the moment someone invents a field, and the failure mode is patient text in an aggregator with no retention policy.
- `RXAUTH_LOG_SOURCE_TEXT=true` is refused unless `RXAUTH_ENVIRONMENT=local`, and a test runs a whole case and asserts that **no quoted span from any document reached any log handler**. The allow-list is the guard rail; that test is the guarantee.
- Latency lives in the logs, never in `reports/`. The committed reports are evidence and are gated on reproducing exactly, so a duration would make them differ every run for reasons that say nothing about correctness.
- The classifier artifact is no longer a pickle. It is a directory — `model.json`, `weights.npz`, `manifest.json` — that is readable without executing anything, records what trained it (data fingerprint, split sizes, metrics, library versions), and is SHA-256 verified on load. A tampered or truncated artifact is refused; a scikit-learn minor-version change warns rather than refuses, because reconstruction uses documented fitted attributes.
- The round trip is exact: a test asserts a reconstructed model reproduces every prediction to zero absolute tolerance.
- `rxauth-check-reports` now gates ten reports, including the classifier report, whose latency is stated in prose rather than a table cell.

### Repository layout

```text
.
├── src/rxauth_ai/    # installable application package and CLI entry points
├── tests/            # automated tests
├── data/             # synthetic document corpus, policy corpus, gold sets, case packets
├── reports/          # reproducible evaluation artifacts
├── docs/             # milestone and architecture documentation
├── pyproject.toml    # package, dependency, test, and lint configuration
└── uv.lock           # reproducible dependency lock
```

## 1. What it is

RxAuth AI is a **human-in-the-loop** prior-authorization intelligence platform for specialty-pharmacy workflows. Given a patient case, it ingests the supporting documents, classifies them, extracts structured evidence, retrieves the applicable payer policy, converts that policy into structured requirements, matches the evidence against those requirements, flags what's missing or ambiguous, drafts a citation-grounded response, validates the draft for unsupported claims, and presents everything to a human reviewer before submission.

The portfolio version uses **public payer-policy documents and fully synthetic / de-identified patient cases only.** The system does not diagnose, prescribe, recommend treatment, or make autonomous clinical decisions. It is administrative decision-support: it prepares a case for a human, it does not decide the case.

---

## 2. Why this project

**Become a stronger AI engineer.** The project demonstrates the full AI-engineering lifecycle — data → preprocessing → representation → training → evaluation → inference → RAG → agent orchestration → AI evaluation → human feedback → model/prompt improvement → production — not just calling an LLM API. The center of gravity is Python, ML, deep learning, retrieval, evaluation, and AI-system design. The frontend is polished but is not the primary learning objective.

**Build on real domain experience.** Specialty-pharmacy technology (River's Edge), healthcare software and HIPAA-regulated workflows (River's Edge, NexteHealth), React/TypeScript, Node APIs, SQL-backed healthcare apps, Python/Flask, OpenAI API, Azure AI Foundry, FHIR, OpenCV, and auth/RBAC. That makes RxAuth a logical evolution of an existing background rather than an unrelated exercise.

**Create something that could eventually make money.** The thesis is *not* to replace electronic PA networks — it's to improve the intelligence and completeness of a specialty-drug PA case *before* it enters the existing submission workflow: which policy applies, what it requires, which requirements the record already supports, what's missing, and where every answer came from.

---

## 3. Guardrails

RxAuth AI is an administrative decision-support system. Five constraints apply to the whole project:

- **Administrative scope only.** It does not determine medical necessity independently, recommend a drug, diagnose a patient, replace a pharmacist or clinician, approve or deny treatment, or make an autonomous medical-necessity decision.
- **Synthetic / de-identified data only.** Public payer policies plus synthetic patient cases in the portfolio version — never real PHI. A commercial deployment would need a separate HIPAA-ready architecture (see §19).
- **Do not overclaim whether PA is required.** Public policy documents alone can't establish a real member's live benefit status. In the portfolio version the "PA required" state comes from a **synthetic benefit/claim trigger or explicit user input**, never inferred from policy text.
- **No fabricated metrics.** Every number published in `/reports` comes from a reproducible run; models needing a defensible labeled dataset stay roadmap items until that data exists.
- **Human review is mandatory.** Low-confidence or ambiguous results route to a reviewer. Generated content is never treated as submission-ready just because a model produced it.

---

## 4. Certificate mapping (the incremental build overlay)

The one addition to the plain architecture: each AI component below is annotated with the IBM AI Engineering course block that motivates it, so the project is built *as the coursework progresses* and the repo reads as a learning log.

| Course block | RxAuth component it unlocks |
|---|---|
| Python for data / working with data | Document ingestion pipeline (§7) |
| Machine Learning with Python (scikit-learn) | Classifier baseline — TF-IDF + LogReg (§8, Phase 1) |
| Deep Learning (Keras / PyTorch) | Transformer document classifier + baseline comparison (§8, Phase 2) |
| CV / NLP; model optimization | Information extraction with confidence (§9) |
| LLM applications, RAG, vector databases | Payer-policy RAG (§10) + criteria extraction (§11) |
| AI agents with RAG & LangChain (GenAI capstone) | LangGraph workflow, generation, groundedness gate (§12–14) |
| Model evaluation | Evaluation suite (§15) + human-in-the-loop feedback (§16) |

---

## 5. End-to-end workflow

```text
Synthetic case + documents
   → Python document processing (parse, preprocess, normalize)
   → Document classification (ML baseline → deep model)
   → Clinical / administrative extraction → Patient Evidence Store
   → Payer policy retrieval (metadata filter + vector search)
   → Policy criteria extraction (prose → structured requirements)
   → Criteria ↔ evidence matching (deterministic + model-assisted)
   → Missing-evidence detection
   → Draft PA responses (source-grounded)
   → Groundedness / safety evaluation
   → Human review → Approved PA preparation package
```

---

## 6. High-level architecture

```text
                Next.js reviewer dashboard
                          │
                 Application API (auth / cases / files)
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
 Python AI service   PostgreSQL         Object storage
 ML / DL             cases              (synthetic docs)
 extraction          evidence
 RAG                 criteria
 evaluation          model runs
        │             eval results
        └───────┬──────────┘
                ▼
        pgvector — payer policies
```

**Hybrid AI principle:** use probabilistic AI where interpretation is required, deterministic Python where a rule can be represented explicitly. A policy line like "≥ 12 weeks of Drug A" is extracted by the model into a structured rule (`operator: >=, duration: 12, unit: weeks`), then evaluated in plain Python (`16 >= 12 → SATISFIED`). This keeps business logic out of the language model.

---

## 7. Component — Document ingestion
*Course: Python for data.* Ingest PDF/image documents, preprocess (deskew, denoise, normalize), and build a labeled **synthetic** dataset across classes (clinical note, lab result, prescription, medication history, insurance document, referral, PA form, other). Reuses Flask / pypdf / OpenCV / Pydantic. **Deliverable:** reproducible dataset builder in `/data`.

## 8. Component — Document classification (train, don't buy)
*Courses: ML with Python, then Deep Learning.*
- **Phase 1 baseline:** TF-IDF + Logistic Regression. Report accuracy, precision, recall, F1, confusion matrix, inference latency.
- **Phase 2 deep model:** transformer-based classifier, compared scientifically against the baseline — dataset construction, train/val/test split, leakage prevention, class imbalance, overfitting, failure cases, latency, deployment tradeoffs.
**Deliverable:** `reports/classifier_baseline.md` and `reports/classifier_deep_vs_baseline.md`.

## 9. Component — Information extraction
*Courses: CV/NLP, model optimization.* Convert unstructured documents into normalized evidence, attaching to every field: source document, page, source span, and extraction confidence. Never store a normalized value without provenance. Low-confidence fields route to human review. **Deliverable:** extraction service + confidence-calibration note.

## 10. Component — Payer-policy RAG
*Courses: LLM apps, RAG, vector DBs.* Ingest **public** payer PA policies (parse → clean → section-detect → chunk → attach metadata → embed → pgvector). Retrieval prefers **metadata filtering + semantic similarity**, not vector search alone. **Deliverable:** retrieval service with citations surfaced in the UI, plus `reports/policy_retrieval.md` measuring that preference against vector-only search rather than asserting it.

## 11. Component — Criteria extraction
*Course: LLM apps.* Turn policy prose into structured requirements, retaining original text, payer, policy version, effective date, page, and extraction model/prompt version. A requirement the extractor cannot structure is retained and routed to a reviewer, never dropped — a policy evaluated against fewer criteria than it states reads as readier than it is. **Deliverable:** criteria extractor + structured criteria store + `reports/criteria_extraction.md`.

## 12. Component — Criteria-to-evidence matching (core intelligence)
For each criterion: retrieve relevant evidence → normalize → deterministic check where possible → model-assisted interpretation where necessary → assign a result from `SATISFIED / NOT_SATISFIED / MISSING / AMBIGUOUS / HUMAN_REVIEW_REQUIRED`. Ambiguity is a first-class outcome ("used for several months" → AMBIGUOUS, duration not explicit enough for a deterministic check). **Deliverable:** matching engine + evaluation report.

## 13. Component — Agentic workflow (LangGraph)
*Course: AI agents with RAG & LangChain.*

**Architecture decision:** the *top-level* architecture stays deliberately linear and simple — one obvious flow from document packet to reviewer (§5). The multi-stage reasoning is not exposed as separate top-level services; it lives *inside* a single controlled LangGraph workflow. This is a conscious choice over a more service-fragmented design: fewer moving parts for V1, one place where provenance and versioning are enforced, and no autonomous top-level orchestration.

The agent is an explicit state graph, not a free-running loop:

```text
START → validate_case → resolve_pa_trigger → resolve_payer_drug_indication
      → retrieve_policy → extract_policy_criteria → retrieve_case_evidence
      → normalize_requirements_and_evidence → evaluate_deterministic_rules
      → model_assisted_interpretation_for_ambiguity → identify_missing_evidence
      → generate_requirement_checklist → run_groundedness_and_citation_checks
      → human_review → END
```

Each node has typed input/output, structured logging, source provenance, model/prompt version where applicable, an explicit failure state, retry behavior only where safe, and **no autonomous submission**.

## 14. Component — Draft generation + groundedness gate
The generator drafts only from approved evidence and never invents diagnoses, dates, medications, durations, lab values, or requirements — missing evidence yields "Human review required," not a guess. A groundedness pass then scores every claim against patient evidence and policy source before the reviewer sees it (`grounded / partially grounded / unsupported / conflicting / requires review`), optionally complemented by Ragas. **Deliverable:** `reports/generation_faithfulness.md`.

## 15. Evaluation strategy (first-class, not an afterthought)
A synthetic gold-standard dataset with expected policy, criteria counts, and statuses per case. Measured per layer: **retrieval** (Recall@K, Precision@K, MRR, correct-policy rate); **classification** (P/R/F1, confusion matrix); **extraction** (exact match, field-level P/R, normalized-value accuracy); **matching** (correct evidence selection, false-support rate, missing-evidence recall, ambiguity detection); **generation** (citation correctness, grounded-claim rate, unsupported-claim rate); **production** (end-to-end + inference latency, embedding/completion cost, failure rate).

## 16. Human-in-the-loop feedback
Reviewer corrections become engineering data: store original output, corrected value, source evidence, model version, prompt version, and reviewer action — feeding regression tests, evaluation datasets, prompt improvement, and eventual retraining.

---

## 17. Data model (structured, not prompt-held)

`Case`, `Document`, `Evidence`, `Policy`, `Criterion`, `CriterionEvaluation` as typed entities. Every extracted value carries provenance (document, page, span, confidence, method); every criterion carries its source (payer, version, page, source text); every evaluation carries supporting evidence IDs, confidence, method, and explanation.

## 18. Engineering discipline

- **Model & prompt versioning** — every AI result records which classifier, extraction model/prompt, embedding model, and generation prompt produced it, so "did draft-v7 reduce unsupported claims vs v6?" is answerable.
- **Observability** — structured logs per request: `request_id, case_id, workflow_node, model, version, prompt_version, token counts, latency_ms, estimated_cost, retrieved_document_ids, evaluation_result, error_type`.
- **Portfolio deliverables** — README, architecture + AI-workflow diagrams, synthetic-data docs, model card, evaluation report, security/privacy statement, screenshots, demo video, API docs, tests, plus ADRs for meaningful decisions (pgvector, deterministic evaluation for explicit criteria, synthetic-only data, stateful graph over free-form agent, evidence provenance, human approval before completion).

## 19. Security & privacy

**Portfolio version:** public payer policies + synthetic identities, notes, prescriptions, and insurance records only. **Never** real patient documents, real PHI, copied records, or production screenshots. A commercial version would require an explicit HIPAA program (compliant infrastructure, access control, encryption, audit logging, secrets management, retention policy, BAAs, incident response, least privilege, PHI-safe logging) — documented as a deployment prerequisite, not implemented against real data. The public repo states plainly that it is a portfolio/research prototype, not a production medical system.

---

## 20. What NOT to build first

Explicitly out of scope for V1, to protect against scope creep: real EHR / PBM / payer / pharmacy-system integration, autonomous submission, real patient data, multi-agent architecture, Kubernetes, dozens of payers or medications, the denial-prediction model (§22), a mobile app, an MCP server (§21), and production billing. The flagship value of V1 is the *quality of the AI workflow*, not feature count.

## 21. Stretch goal — MCP server

Only after the V1 workflow is reliable, expose narrow capabilities as MCP tools so the pipeline is callable from any MCP client:

```text
classify_document()
search_policy()
get_policy_criteria()
extract_case_evidence()
evaluate_criterion()
draft_requirement_checklist()
```

MCP should demonstrate interoperability — it must not become a reason to delay the core AI pipeline. This is the rare-signal differentiator (few candidates have *built* an MCP server rather than used one), which is exactly why it's a reward for finishing V1, not a V1 task.

## 22. Deferred goal — PA denial-risk model

A denial-risk model requires a defensible labeled dataset and stays deferred until such data exists. With a legitimate dataset, it could later estimate *administrative* rejection risk (missing documentation, incomplete evidence, inconsistent fields) and surface which features drove the score. It must **not** be presented as a clinically predictive model, and must never be trained on fabricated labels and then marketed as validated.

## 23. Milestone 0 — "One case, end to end"

Before any production UI: 1 synthetic patient, 1 medication, 1 payer, 1 public policy, 3–5 synthetic documents, 4–8 criteria. A Python-only pipeline that outputs structured criterion evaluations with status, patient evidence, policy source, and confidence per criterion. Only after this works does the application layer become a focus.

## 24. First engineering questions

Before building broad product functionality, resolve these through small experiments — they should drive the earliest notebooks:

1. What is the smallest useful document taxonomy?
2. What synthetic dataset is sufficient for a meaningful baseline classifier?
3. What preprocessing materially improves classification/extraction?
4. Which policy structure is easiest to normalize?
5. What chunking strategy retrieves exact PA criteria most reliably? *(§10: one chunk per enumerated requirement — see `docs/phase-4-policy-rag.md`.)*
6. Which metadata fields should be mandatory before vector search? *(§10: payer, normalized medication, indication, and the effective/superseded version window; a policy missing any of them is rejected at parse time.)*
7. Can payer criteria be reliably converted to structured JSON?
8. Which criteria can be evaluated deterministically?
9. How should ambiguous evidence be represented?
10. What confidence thresholds best route extraction to human review?
11. What counts as a grounded requirement-checklist statement?
12. Which evaluation metrics best predict reviewer trust?

## 25. Final principle

> **Do not build an AI that merely sounds confident. Build an AI system that can prove where every important answer came from, measure when it is wrong, and route uncertainty to a human.**

That principle governs the architecture, interface, evaluation strategy, and commercial positioning.

---

## Roadmap status

- [x] Milestone 0 — one case, end to end (Python-only)
- [x] Phase 1.5 — ingestion pipeline + hardened synthetic benchmark (§7)
- [x] Classifier baseline (§8, Phase 1)
- [x] Deep-learning classifier + comparison (§8, Phase 2) — baseline retained after three-seed comparison
- [x] Information extraction with confidence (§9) — medication normalization, multi-page/OCR challenge coverage, multi-span and cross-document provenance, calibrated review routing, and deterministic-vs-learned comparison
- [x] Real-document case assembly — the Milestone 0 spine now runs on ingested, classified, and extracted documents instead of fixtures
- [x] Payer-policy RAG (§10) + criteria extraction (§11) — metadata-filtered retrieval with a measured ablation against vector-only search, policy-version selection driven by an extracted request date, and prose-to-structured criteria that retain, flag, and route what they cannot check
- [x] Criteria-to-evidence matching (§12) — hybrid retrieval, unit normalization, five-state results, and a 42-record gold set scoring the cited evidence alongside the status
- [x] Workflow state graph (§13) — thirteen named nodes over typed state, per-node status, component versions, and an explicit failure state ([ADR 001](docs/adr-001-workflow-runtime.md) records why the executor is hand-written rather than LangGraph)
- [x] Draft generation + groundedness gate (§14) — a cited requirement checklist behind a claim-level gate that rejects any value or medication absent from the spans the claim cites
- [x] Evaluation suite (§15) — `rxauth-evaluate` scores 22 metrics across six layers against ratcheted thresholds and fails CI on a regression
- [x] Human-in-the-loop feedback (§16) — typed, append-only reviewer decisions that export as matching-gold records
- [ ] Reviewer UI (Next.js) — next
- [~] Production hardening — typed settings, §18 structured logging with a PHI-safe guarantee, and pickle-free versioned model artifacts are done; persistence, API, and containers are next
- [ ] *Later, not first:* denial-risk model (only with real labeled data), MCP server
