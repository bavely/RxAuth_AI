# RxAuth AI — IBM AI Engineering Course Project

## Presenter guide

This deck is designed for a 12–15 minute presentation plus questions. The PowerPoint file contains the same notes in each slide’s Notes pane. Use the appendix only when asked about reproducibility or evidence.

### 1. Title

RxAuth AI is an evidence-grounded prior-authorization intelligence prototype. It begins as an IBM AI Engineering course project and grows through classical machine learning, deep learning, retrieval, evaluation, and controlled agentic workflows. Its defining principle is that important answers must be traceable and uncertainty must route to a human.

### 2. Executive summary

Prior authorization is an administrative workflow with heterogeneous documents and policy-specific evidence requirements. Phase 1.5 implements typed ingestion, a leakage-resistant classifier baseline, deterministic matching, provenance, groundedness checks, reproducible reports, and tests. Deep learning and RAG are explicitly roadmap items.

### 3. Problem

A useful system must do more than summarize. It must identify the applicable requirement, locate patient evidence, normalize it, decide whether an explicit rule can be computed, and show its sources. Missing and ambiguous information must remain visible.

### 4. Scope and safety

The portfolio uses synthetic data only and supports administrative preparation. It does not diagnose, prescribe, approve, deny, infer live benefits, or submit autonomously. Human review is mandatory. A real deployment would require a separate HIPAA-ready architecture and governance program.

### 5. Workflow

The top-level user journey is linear: ingest, classify, extract, retrieve policy, structure criteria, match evidence, run a groundedness gate, and review. Phase 1.5 implements ingestion, classification, the matching spine, and a structural groundedness gate. The remaining stages are labeled roadmap rather than presented as complete.

### 6. Typed data model

Pydantic entities keep important state outside prompts. Evidence records normalized values with document, page, source span, method, and confidence. Criteria preserve the policy source. Evaluations retain both sources, selected evidence IDs, result, method, confidence, and explanation.

### 7. Ingestion

The system supports text, Markdown, text-bearing PDFs, and common image formats. Images pass through grayscale conversion, denoising, deskewing, and Otsu thresholding before an OCR adapter. The benchmark includes 16 PDFs and 16 scan-like images. PDF character error rate is 0.000, preprocessing succeeds on all images, and OCR accuracy is intentionally not claimed without a configured runtime.

### 8. Dataset design

The synthetic classifier corpus has 480 documents across eight balanced classes. Case IDs and template families do not overlap across train, validation, test, and challenge partitions. The loader rejects leakage. The challenge family includes unseen framing, cross-class noise, and deterministic OCR-like corruption and is used for robustness analysis—not model selection.

### 9. Baseline model

TF-IDF creates sparse unigram and bigram features, and balanced logistic regression estimates eight class probabilities. The baseline is fast, reproducible, and easy to compare with a future transformer. A 0.65 confidence threshold routes uncertain predictions to review; it is a synthetic benchmark policy, not a clinically validated threshold.

### 10. Classifier results

Grouped test macro F1 is 0.979, with one error among 48 examples. The challenge macro F1 is 0.916, with four errors. Reported CPU batch inference latency is about 0.004 milliseconds per document. These results establish a benchmark on synthetic data and do not imply production accuracy.

### 11. Calibration and routing

The model’s test accuracy is 97.9%, but mean confidence is only 56.1%, expected calibration error is 0.418, and 68.8% of predictions route to review. The model is often correct but under-confident. Calibration and threshold analysis are therefore more valuable next steps than optimizing headline accuracy alone.

### 12. Criteria matching

Matching returns SATISFIED, NOT_SATISFIED, MISSING, AMBIGUOUS, or HUMAN_REVIEW_REQUIRED. Explicit numeric and outcome rules execute in Python. Low-confidence evidence, incompatible units, or nonnumeric text required for a numeric comparison route to a human instead of being guessed.

### 13. Demo case

PA-DEMO-001 includes five documents and six criteria. Four criteria are supported, one screening document is missing, and one duration is ambiguous because the source says “several months.” Each result includes policy provenance, patient provenance where relevant, method, confidence, and explanation. The groundedness gate passes.

### 14. Groundedness

The current structural gate requires policy provenance for every evaluation and patient evidence for any supported or not-satisfied claim. When generated drafting is added, this contract expands to semantic faithfulness, citation correctness, conflict detection, and unsupported-claim evaluation.

### 15. Engineering discipline

The current repository has 21 passing tests. It is an installable Python 3.12 package with locked dependencies, deterministic data generation, CLI entry points, serialized artifacts, and versionable reports. Limitations and non-claims are documented alongside results.

### 16. IBM course alignment

Each IBM learning block unlocks a measurable deliverable: data engineering becomes ingestion, machine learning becomes the baseline, deep learning becomes a transformer comparison, CV and NLP become extraction, RAG becomes public policy retrieval, agents become a controlled state graph, and evaluation spans every layer.

### 17. Roadmap

The immediate next step is a transformer classifier on the same partitions and reporting contract. Compare macro F1, challenge robustness, calibration, review routing, artifact size, latency, and deployment cost. Then add extraction, public policy RAG, criteria extraction, controlled generation, and a reviewer UI.

### 18. Conclusion

The project’s main accomplishment is not a claim that synthetic data solves prior authorization. It is a defensible engineering foundation: typed contracts, leakage-resistant experimentation, explicit uncertainty, deterministic business logic, provenance, human review, and reproducible evidence.

### 19. Appendix — commands

Use this slide if asked how to reproduce results. The commands rebuild both corpora, run both benchmarks, execute the end-to-end case, and run the test suite.

### 20. Appendix — source map

Use this slide if asked to verify a metric or design claim. Every number and architectural statement in the deck maps to a repository document, report, JSON artifact, or executable test.
