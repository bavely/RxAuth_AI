# RxAuth AI — Milestone 0: "One case, end to end"

Python-only, offline proof of the pipeline **spine** for a single synthetic prior-authorization case. No database, no LLM, no network — so it runs anywhere and demonstrates the architecture before any of the trained-model or retrieval work begins.

This is the milestone the main README (section 23) requires *before* any production UI: 1 synthetic patient, 1 medication, 1 payer, 1 policy, several documents, and 6 criteria that deliberately exercise every one of the five result states.

## What it proves

The core design principles are all live here, even though the ML/RAG components are stubbed by synthetic fixtures:

- **Deterministic-first (main README §7).** Explicit rules (`16 >= 12`, `7.4 < 8.0`) are evaluated in plain Python. The language model is never asked to improvise a conclusion it doesn't need to.
- **Five-state matching (§12).** Every criterion resolves to `SATISFIED / NOT_SATISFIED / MISSING / AMBIGUOUS / HUMAN_REVIEW_REQUIRED`.
- **Uncertainty is routed, not guessed.** Non-numeric evidence ("several months") and low-confidence extractions go to a human — the spot where model-assisted interpretation will later plug in.
- **Provenance on everything.** Every value and every evaluation carries its source (document, page, text span).
- **Groundedness gate (§14).** Nothing is "ready" unless every concrete claim cites patient evidence and a policy source.
- **Synthetic-only guardrail (§3).** All data is fabricated placeholders.

## Run it

```bash
uv sync --group dev
uv run rxauth-milestone0                     # human-readable summary + trace + JSON
uv run rxauth-milestone0 --json-only         # structured output only

uv run rxauth-build-dataset                  # rebuild the synthetic document dataset
uv run rxauth-train-classifier               # train + evaluate TF-IDF + LogReg (§8, Phase 1)
uv run pytest                                # run the complete test suite
```

Milestone 0 expected: 4 supported, 1 needs review (ambiguous), 1 missing, groundedness gate PASS. Structured output lands in `reports/case_PA-DEMO-001.json`.

Classifier baseline expected: ~0.98 test accuracy on the synthetic corpus, one or two genuine confusions (not a perfect score — see the dataset's "Known limitation" note on why that ceiling isn't meaningful for real-world performance). Report lands in `reports/classifier_baseline.md`.

## Layout

```
.
├── src/rxauth_ai/
│   ├── cli.py                    # Milestone 0 CLI entry point
│   ├── train_classifier.py       # §8 Phase 1 CLI entry point
│   ├── build_dataset.py          # reproducible synthetic document generator (§7)
│   ├── models.py                 # typed entities (main README §17)
│   ├── synthetic_case.py         # the one synthetic case + policy fixture
│   ├── matching.py               # deterministic + ambiguity-aware evaluation (§12)
│   ├── groundedness.py           # citation/provenance gate (§14)
│   ├── pipeline.py               # orchestrator → CaseReadinessReport
│   └── classifier.py             # TF-IDF + LogReg train/eval (§8, Phase 1)
├── tests/                        # pipeline and classifier tests
├── data/
│   ├── documents/<class>/*.txt   # generated corpus (8 classes)
│   └── manifest.csv              # doc_id, label, split, ...
├── reports/                      # JSON + classifier_baseline.md output
└── pyproject.toml                # dependencies, CLI commands, and tool config
```

## What is intentionally NOT here yet

Per the main README's "what not to build first," Milestone 0 stubs the components that later phases build for real:

| Stubbed in Milestone 0 | Status now |
|---|---|
| Document classification | ✅ §8 real — TF-IDF + LogReg, retained over the Phase 2 transformer after a three-seed comparison |
| Field/evidence extraction | ✅ §9 real — `regex-v3` with medication normalization, multi-span/cross-document provenance, measured calibration, and a learned baseline comparison |
| Payer-policy retrieval | §10–11 — pgvector RAG + criteria extraction |
| Agent orchestration | §13 — LangGraph workflow |
| Faithfulness scoring | §14 — Ragas on generated text |

The matching engine, five-state contract, provenance model, and groundedness gate built in Milestone 0 are the real thing and carry forward unchanged.

**Scope note on §7:** Milestone 0's `rxauth-build-dataset` generated document *text* directly, standing in for what a real OCR/PDF-extraction step would output. Phase 1.5 closed that gap: the builder now also renders deterministic PDFs and scan-like PNGs, and `ingestion.py` handles text, text-PDF, and image inputs behind one page-level contract. See [phase-1.5.md](phase-1.5.md).

## Where the spine stands now

`rxauth-milestone0` still runs the original fixture, and it is still the clearest picture of the flow. But the same spine now runs on real files:

```bash
uv run rxauth-run-case data/cases/PA-CASE-001
```

That run ingests, classifies, extracts, resolves, matches, and gates a real document packet — and lands on the exact criterion profile this fixture produces (4 supported, 1 missing, 1 ambiguous). The equivalence is an acceptance test, not a demo: swapping fixtures for real components must not change what the reviewer is told. See [case-assembly.md](case-assembly.md).

## Next step

**§10–11 — payer-policy retrieval and criteria extraction.** The policy is the last fixture in the flow: `resolve_policy` returns the hand-authored `PA-104`, and the criteria it carries were written by hand rather than extracted from policy prose. Replacing it means ingesting public payer PA policies, chunking with metadata, retrieving by metadata filter plus semantic similarity, and converting the retrieved prose into structured requirements that keep their payer, version, effective date, and page.
