# ADR 002 — Production V1 is synthetic-only; a commercial pilot is a separate gate

**Status:** accepted, 2026-10-02 — approved by the release owner (backlog task 101)
**Context:** README §3 (guardrails), §19 (security & privacy), §20 (what not to build first);
[jira-production-readiness.csv](jira-production-readiness.csv) epics 100–1200

## Context

The prototype is functionally complete against its roadmap: the thirteen-node workflow, the
service layer, durable jobs, tenant isolation, and the reviewer UI all exist and pass CI. The
production-readiness backlog turns that into something that can be operated. Before any of its
96 tasks can be accepted, one question needs a written answer: *production for what data?*

Two very different targets hide behind "production":

1. A deployed, operated, monitored service that still processes **only synthetic cases** and
   public payer policies.
2. A **commercial pilot** that accepts real patient documents for real reviewers.

The second needs everything the first needs, plus a HIPAA program (§19): BAAs, expert privacy
and legal review, incident response with notification duties, real-data governance, and pilot
validation on cases that cannot live in a public repository. Treating them as one release would
either stall the first behind contracts this repository cannot produce, or let the second ship
on evidence that only covers the first.

## Decision

**Production V1 is a synthetic-only deployment.** It is real infrastructure (AWS, PostgreSQL,
S3, an identity provider, monitoring, backups) running the existing pipeline on synthetic data.
Backlog epics 100–1000 and 1200 lead to it, launching at task 1205.

**A commercial pilot is a separate, later gate.** It is epic 1100 (label
`rxauth-commercial-gate`), and no real document is accepted until task 1107 approves it. Passing
production V1 is necessary for the pilot, never sufficient.

Both targets keep every repository invariant. Production changes *where* the pipeline runs and
*who* may reach it — never *what* it is allowed to conclude.

## What does not change in either target

| Boundary | Enforced by |
|---|---|
| **Administrative scope.** No diagnosis, treatment recommendation, approve/deny, or submission. `await_human_review` is the terminal node | `test_workflow.py::test_the_graph_ends_by_naming_what_a_person_still_has_to_do`, `test_generation.py::test_a_checklist_is_never_marked_submittable` |
| **Five results are real answers.** Uncertainty is never collapsed into a guess | `test_generation.py::test_missing_evidence_is_drafted_as_absent_never_guessed`, the matching gold set via `rxauth-evaluate` |
| **Every claim is cited**, and the citation gate is never loosened | the `test_gate_*` tests in `test_generation.py`, including `test_an_injected_generator_is_held_to_the_same_gate` |
| **PA-required is declared input**, never inferred from policy text | `test_workflow.py::test_the_pa_trigger_node_says_the_value_was_declared_not_inferred`, `test_api.py::test_creating_a_case_that_infers_pa_required_is_rejected` |
| **Offline core.** The service layer wraps the workflow; the workflow never reaches the network or the service layer | `test_scope_boundaries.py` (added with this ADR) |
| **Tenant isolation.** Another organization's resource is a 404 | `test_api.py::test_the_same_case_id_is_isolated_between_organizations`, `test_persistence.py::test_runs_and_decisions_are_invisible_across_organizations` |
| **PHI-safe logging.** Source text is logged only when `environment=local` | `test_config.py::test_logging_patient_text_is_refused_outside_a_developer_machine` |
| **Deployed environments fail closed.** Authentication on, S3 storage, strict origins, compliance-mode Object Lock in production | `test_api.py::test_the_api_refuses_to_start_unauthenticated_outside_local`, `test_config.py::test_a_wildcard_origin_is_refused_in_a_deployed_environment`, `test_config.py::test_production_requires_compliance_object_lock_mode` |
| **No fabricated metrics.** Reports are regenerated, gold is never edited to pass, thresholds only ratchet up | `rxauth-check-reports`, `rxauth-evaluate`, the `protect-paths` hook |

Before this ADR, the offline-core rule was a sentence in the README with nothing behind it. It is
the one boundary the move to deployed infrastructure most invites breaking, because a database,
an object store, and an identity provider all arrive at once. `test_scope_boundaries.py` reads the
import graph from `workflow` and `case_assembly` and fails if the core reaches a service module,
a network-capable standard-library module, or any third-party package not declared as a core
dependency in `pyproject.toml`.

## What stays out of scope for both

Unchanged from §20: real EHR, PBM, payer, or pharmacy-system integration; autonomous submission;
multi-agent orchestration; Kubernetes; the denial-risk model (§22, which also lacks labeled data);
an MCP server (§21); a mobile app; billing. Real patient data is out of scope for V1 and gated
by task 1107 for the pilot.

## What V1 does not claim

A synthetic production deployment demonstrates operability, security controls, and recovery. It
does **not** demonstrate accuracy on real clinical documents: every quality number in `reports/`
is measured on synthetic data, and task 508's capability matrix must say so. Nor is it HIPAA
compliance. Its controls are the technical half of §19; the contractual and organizational half
is epic 1100.

## Consequences

- V1 can ship without BAAs, legal review, or real cases, because it never holds PHI. The
  `environment=production` settings must still behave as though it did (fail-closed auth,
  encryption, Object Lock, PHI-safe logs), so the pilot inherits tested controls rather than new ones.
- Every backlog task names which target it serves. Commercial-only work does not block V1.
- Changing either boundary, such as accepting a de-identified real document in V1, means
  superseding this ADR, not editing a task.

## Approval

| Role | Name | Decision | Date |
|---|---|---|---|
| Release owner | Bavely Tawfik | Approved | 2026-10-02 |
