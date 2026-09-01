RxAuth AI — how this project works
What it is
An offline prototype that decides whether a prior-authorization (PA) request is "ready". You hand it a folder of synthetic medical documents; it reads them, works out which insurer policy applies, turns that policy's prose into machine-checkable rules, checks the documents against those rules, and prints a report saying 4 criteria supported, 1 missing, 1 needs a human — with a citation (file, page, exact quoted text) behind every single claim.

No database, no network, no LLM. Everything is Python + scikit-learn + regex, deliberately.

The main flow, start to finish
The one command that runs everything is uv run rxauth-run-case data/cases/PA-CASE-001.

#	What happens	Where
1	Read case.json — the declared facts (payer, drug, condition, pa_required)	case_assembly.py:135
2	For each file in the folder, pull out text. .txt → read directly. .pdf → pypdf. .png/.jpg → OpenCV cleanup then Tesseract OCR. Each page carries a confidence	ingestion.py
3	Classify each document (clinical note? lab report? insurance card?) using the trained TF-IDF model. Low confidence → flagged for human review	classifier.py
4	Run regex rules over the text to pull out Evidence — "16 weeks of Drug A, inadequate response" — each with the file, page, character offsets, and quoted span it came from	extraction.py
5	Clean up the evidence: drop overlapping matches, merge duplicates, link a duration on one line to its outcome on another, and downgrade confidence if the page scan was poor	extraction.py:535-670
6	Note facts that appear in two different documents (corroboration)	case_assembly.py:186
7	Work out the request date — from the manifest, or from the PA request's own extracted date	case_assembly.py:277
8	Find the policy. Filter the corpus by payer + drug + condition + which version was in force on that date. Then rank passages by similarity. Filter first is a safety rule, not an optimisation	policy_retrieval.py
9	Turn the policy's numbered requirements into typed rules: "at least 12 weeks" → operator ">=", value 12, unit "weeks"	criteria_extraction.py
10	Match. For each rule, find relevant evidence, convert units, compare in plain Python, and pick a result from five states	matching.py
11	Groundedness gate — refuse to call anything "satisfied" unless it cites both a document span and a policy span	groundedness.py
12	Print the report and write reports/case_PA-CASE-001.json	pipeline.py + cli.py:29
The five results a criterion can get: SATISFIED, NOT_SATISFIED, MISSING, AMBIGUOUS, HUMAN_REVIEW_REQUIRED. The whole design exists to make the last three real answers rather than failures — the system never guesses to avoid saying "I don't know."

The 10 files to understand first
models.py — every shape in the system: Document, Evidence, Criterion, Policy, Case, CriterionEvaluation, CaseReadinessReport. Read this first; everything else moves these objects around.
case_assembly.py — the real end-to-end runner. Read run_case() and you've seen the whole app.
pipeline.py — only ~100 lines; the spine that turns a Case + Policy into a report.
matching.py — the core intelligence. Where a rule meets a fact.
extraction.py — biggest file (808 lines). Regex rules + the four cleanup stages.
ingestion.py — files in, text out. Small and self-contained.
criteria_extraction.py — policy English → typed rules.
policy_retrieval.py — picking the right policy version.
policy_corpus.py — chopping a policy file into citable chunks.
classifier.py — the trained document-type model.
Honourable mention: groundedness.py is 58 lines and worth reading in one sitting — it's the project's conscience.

Every file that matters, and what breaks if you touch it
The spine — change these and the whole app changes
File	Controls	Connects to	Blast radius
models.py	All data shapes	Imported by everything	Highest risk in the repo. Adding an optional field is safe. Renaming or making a field required breaks every module, every test, and invalidates reports/*.json
pipeline.py	Report assembly, tallies, the "ALL vs ANY" refusal	← cli, case_assembly; → matching, groundedness	Changes every number in every report and in test_pipeline.py
case_assembly.py	Real end-to-end run, case.json schema, cross-doc linking, rxauth-run-case	→ ingestion, classifier, extraction, policy_retrieval, criteria_extraction, pipeline	Breaks the flagship command and test_case_assembly.py (315 lines)
matching.py	Evidence retrieval, unit conversion, comparison, five-state results	← pipeline; → medications, models	Changes criterion outcomes, so it changes the readiness report. Currently being rewritten (uncommitted)
groundedness.py	PASS/FAIL citation gate	← pipeline	Loosening it lets uncited claims through — the one guarantee the project sells
The reading layer
File	Controls	Connects to	Blast radius
ingestion.py	Text/PDF/image → pages + confidence	→ classifier, extraction, benchmarks	Confidence values flow into every downstream score. OCR changes shift extraction confidence, which can flip a criterion result
extraction.py	Regex rules, provenance, confidence, overlap/merge/link resolution	→ medications, models; ← case_assembly, calibration, benchmarks	Adding a rule can create overlaps with existing rules. Re-run rxauth-benchmark-extraction and rxauth-calibrate-extraction after any change
medications.py	Brand↔generic alias list (50 lines)	→ extraction, matching, policy_corpus	Small file, wide reach. A new alias changes what extraction finds, what matching considers relevant, and which policies retrieval filters in
classifier.py	TF-IDF model, training, evaluation, classify_path	← case_assembly (via a protocol), train_classifier	Retraining changes artifacts/classifier_baseline.pkl and the review-flag counts
The policy layer
File	Controls	Connects to	Blast radius
policy_corpus.py	Parsing policy files: metadata, pages, sections, enumerated items → chunks	→ medications; ← policy_retrieval, criteria_extraction	Changing chunking changes what retrieval returns and what criteria extraction sees. Both gold benchmarks shift
policy_retrieval.py	Metadata filter + TF-IDF ranking, version-window logic	→ policy_corpus; ← case_assembly	Weakening the filter risks returning another payer's policy — the exact failure benchmark_retrieval exists to catch
criteria_extraction.py	Policy prose → typed Criterion, exclusions, ALL/ANY detection	→ policy_corpus, models; ← case_assembly	A dropped requirement makes a case look readier than it is. Unmatched items must stay as unstructured, not disappear
Data you'd realistically edit
Path	What it is	Blast radius
data/policies/	8 synthetic policy text files. Strict format: metadata header, --- page N --- breaks, SECTION N. headings, numbered items	Format deviations break parsing. PA-207 is a deliberate near-miss trap for retrieval — don't "fix" it
data/cases/PA-CASE-001/	The demo packet: case.json + 5 documents	Editing documents changes extraction, which changes the report and test_case_assembly.py expectations
data/*_gold.jsonl	Hand-labelled answer keys for extraction, retrieval, criteria	Editing gold to make a benchmark pass is self-deception — the files exist to catch you
data/manifest.csv	Which generated documents belong to which train/val/test split	Regenerated by rxauth-build-dataset. Hand-editing risks split leakage
Supporting cast (read when relevant, rarely edit)
cli.py — pretty-printer for the report; print_report is reused by case_assembly
synthetic_case.py — the hand-written fixture case for rxauth-milestone0
build_dataset.py / rendering.py — generate the synthetic corpus and fake scans
deep_classifier.py — optional DistilBERT experiment; needs uv sync --extra deep
calibration.py / compare_extractors.py — "are our confidence numbers honest?" and "would ML beat the regexes?"
benchmark_*.py (5 files) — one per stage; each writes a markdown report to reports/
tests/ — 15 files. test_case_assembly.py and test_extraction.py are the ones that catch real regressions
Ignore these entirely
.venv/, .ruff_cache/, .pytest_cache/, __pycache__/, dist/, uv.lock, artifacts/ (build outputs, gitignored), data/documents/** and data/rendered/** (~500 generated files), docs/*.pptx, docs/*.pdf, docs/build_*.ps1 (presentation assets).

Two things worth knowing before you change anything
Work is in progress right now. matching.py has a large uncommitted rewrite (typed-match-v1 → evidence-match-v2), plus new untracked benchmark_matching.py and tests/test_matching.py. benchmark_matching.py expects data/matching_gold.jsonl, which doesn't exist yet.

The dependency chain is one-directional. models.py → everything. Nothing imports case_assembly or cli. So changes get safer the further down the flow you go: touching cli.py affects display only; touching models.py affects all 27 modules.

Now, on saving this — docs/code-walkthrough.md already exists and is 100 KB: a line-by-line reference for every function in the package, cross-linked from the other docs. It's the file you have open. I don't want to destroy that, so I'll insert this short map as a new section at the top of that file rather than overwrite it.