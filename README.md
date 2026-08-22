# RxAuth AI
## Specialty Pharmacy Prior Authorization Intelligence Copilot

> **Status:** Architecture / planning stage.
> **Goal:** One flagship AI-engineering project that begins as IBM AI Engineering coursework, grows into a portfolio system, and has a credible path to a commercial pilot — built incrementally so the commit history traces the progression from classical ML through deep learning to RAG and agentic systems.

**Author:** Bavely S. Tawfik — [pavli-tawfik.com](https://pavli-tawfik.com) · [linkedin.com/in/bavelytawfik](https://www.linkedin.com/in/bavelytawfik) · [github.com/bavely](https://github.com/bavely)

---

## Quick start

The current implementation is a Python 3.12 prototype with an offline Milestone 0 pipeline and a reproducible classical-ML document classifier.

```bash
uv sync --group dev
uv run rxauth-milestone0
uv run rxauth-train-classifier
uv run pytest
```

The synthetic classifier corpus is checked in for reproducibility. Rebuild it with `uv run rxauth-build-dataset`. See [the Milestone 0 guide](docs/milestone-0.md) for expected output and implementation details.

### Repository layout

```text
.
├── src/rxauth_ai/    # installable application package and CLI entry points
├── tests/            # automated tests
├── data/             # synthetic corpus and manifest
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
*Courses: LLM apps, RAG, vector DBs.* Ingest **public** payer PA policies (parse → clean → section-detect → chunk → attach metadata → embed → pgvector). Retrieval prefers **metadata filtering + semantic similarity**, not vector search alone. **Deliverable:** retrieval service with citations surfaced in the UI.

## 11. Component — Criteria extraction
*Course: LLM apps.* Turn policy prose into structured requirements, retaining original text, payer, policy version, effective date, page, and extraction model/prompt version. **Deliverable:** criteria extractor + structured criteria store.

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
5. What chunking strategy retrieves exact PA criteria most reliably?
6. Which metadata fields should be mandatory before vector search?
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
- [ ] Ingestion pipeline + synthetic dataset (§7) — text-level synthetic corpus done; PDF/image rendering + OpenCV preprocessing still open
- [x] Classifier baseline (§8, Phase 1)
- [ ] Deep-learning classifier + comparison (§8, Phase 2)
- [ ] Information extraction with confidence (§9)
- [ ] Payer-policy RAG (§10) + criteria extraction (§11)
- [ ] Criteria-to-evidence matching (§12)
- [ ] LangGraph workflow (§13)
- [ ] Draft generation + groundedness gate (§14)
- [ ] Evaluation suite (§15)
- [ ] Human-in-the-loop feedback (§16)
- [ ] Reviewer UI (Next.js)
- [ ] Production hardening (versioning, observability, Docker, CI)
- [ ] *Later, not first:* denial-risk model (only with real labeled data), MCP server
