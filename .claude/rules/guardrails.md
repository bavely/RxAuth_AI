# Domain guardrails (README §3, §19, §20)

These are product constraints, not style. A change that violates one is wrong even if
every test passes.

## Scope
- Decision-support only. Never add code that diagnoses, recommends a drug, determines
  medical necessity, approves/denies, or submits to a payer.
- `pa_required` is read from `case.json` / the manifest (`workflow.resolve_pa_trigger`).
  Never infer it from policy text.
- Out of scope for V1 (§20). Don't add these unprompted: real EHR/PBM/payer
  integrations, autonomous submission, multi-agent orchestration, Kubernetes,
  denial prediction, an MCP server, billing.

## Uncertainty is an answer
- `MISSING`, `AMBIGUOUS`, and `HUMAN_REVIEW_REQUIRED` are correct outputs. Don't add
  defaults, fallbacks, or "best guess" branches that turn them into `SATISFIED` or
  `NOT_SATISFIED`.
- Low-confidence classification/extraction must route to review, not be dropped.
- Policy items `criteria_extraction` cannot structure stay `unstructured`. Dropping
  one makes a case look readier than it is.

## Citations
- Every `Evidence` carries document, page, char span, quoted text, confidence, method.
- Generated sentences may only state values and medications present in the spans they
  cite (`groundedness.check_draft_groundedness`). Don't relax the gate. Fix the
  generator or the evidence.

## Data
- Synthetic / de-identified data only. Never paste, generate, or commit realistic PHI
  (real names + DOB + MRN combinations, real member IDs).
- `data/policies/PA-207*` is a deliberate retrieval near-miss trap. Do not "fix" it.
- `data/reviewer_feedback.jsonl` is runtime output and gitignored. Don't read it into
  context or commit it.

## Logging
- Use `observability.log_event(...)` with structured fields. Quoted spans or document
  text may only be logged when `settings.log_source_text` is true, which `config.py`
  permits only in `environment=local`. Never bypass that check with `print` or a raw
  logger.
