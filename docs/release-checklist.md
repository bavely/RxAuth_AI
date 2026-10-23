# Release checklist — requirements to evidence

**Status:** accepted, 2026-10-22 (backlog task 103). Approved by the release owner; proposed 2026-10-13.
**Context:** [AGENTS.md](../AGENTS.md) invariants, [ADR 002](adr-002-production-v1-scope.md),
[threat model](threat-model.md), [jira-production-readiness.csv](jira-production-readiness.csv)

## What this is for

Task 1204 approves the production V1 release decision. It has to answer one question per
requirement: *what proves this holds, and who accepted that proof?* This document is that
mapping. It lists every repository invariant and every backlog workstream, the evidence that
exists today, and the tasks still open before that evidence is complete.

The checklist is not evidence by itself. A row is **closed** only when its open tasks are Done in
the backlog and the release owner has accepted the evidence they produced. Until then the row
stays open, however much of it already passes CI.

`tests/test_doc_references.py` keeps this document honest:

- every test reference it cites (module `::` test name) must exist;
- every AGENTS.md invariant and every backlog epic must have a row;
- every task in an **Open tasks** cell must exist in the backlog and must not already be Done.

When a task is marked Done, CI fails until its evidence moves out of the **Open tasks** column.
That is deliberate: it keeps the checklist from going stale.

## Ownership

ADR 002 names one accountable **release owner**, and every row below defaults to that role. Task
102 records accepted Balanced service objectives and founder-led ownership in
[service-objectives.md](service-objectives.md). Bavely Tawfik owns release, operations,
recovery and cost. A technical backup and exact coverage/escalation remain pre-launch
requirements in task 806. The release owner stays accountable for each workstream. The backlog deliberately carries
no assignees.

## Repository invariants

I1–I8 are the AGENTS.md invariants, numbered as they are there. I9 and I10 are the two boundaries
ADR 002 adds for deployed environments.

| ID | Invariant | Evidence in CI today | Gap before release | Open tasks | Owner |
|---|---|---|---|---|---|
| I1 | **Administrative scope** — no diagnosis, treatment, approve/deny or submission; `await_human_review` is terminal | `test_workflow.py::test_the_graph_ends_by_naming_what_a_person_still_has_to_do`, `test_generation.py::test_a_checklist_is_never_marked_submittable`, `test_api.py::test_the_openapi_description_states_the_system_does_not_decide` | Reviewer UAT must confirm the UI presents a prepared case, not a decision; pilot reviewers must be trained on administrative use | 1202, 1106 | Release owner |
| I2 | **Five results are real answers** — uncertainty is never collapsed into a guess | `test_matching.py::test_default_ambiguity_interpreter_abstains_instead_of_guessing`, `test_matching.py::test_conflicting_deterministic_facts_route_to_review_with_both_citations`, `test_matching.py::test_low_confidence_model_interpretation_routes_to_review`, `test_generation.py::test_missing_evidence_is_drafted_as_absent_never_guessed`, `test_evaluation_suite.py::test_the_matcher_is_gated_on_never_claiming_unearned_support` | Review-routing thresholds are set but not validated on independent challenge sets; adversarial coverage not reviewed | 503, 505, 506 | Release owner |
| I3 | **Every claim is cited**, and the gate is never loosened | `test_generation.py::test_gate_catches_a_value_that_appears_in_no_cited_source`, `test_generation.py::test_an_injected_generator_is_held_to_the_same_gate`, `test_persistence.py::test_every_cited_span_survives_the_round_trip`, `test_api.py::test_a_citation_can_be_opened_and_its_span_lands_where_it_claims`, `test_policy_retrieval.py::test_every_retrieved_chunk_carries_a_usable_citation` | Page text and policy sources are not yet served exactly as cited, so a reviewer cannot see every cited span in context | 304, 305, 306 | Release owner |
| I4 | **No fabricated metrics** — reports regenerated, gold never edited to pass, thresholds only ratchet up | `rxauth-check-reports` and `rxauth-evaluate` in the `reports` CI job; `test_check_reports.py::test_an_absent_report_is_drift_not_a_silent_pass`, `test_evaluation_suite.py::test_the_suite_scores_every_layer_and_stays_within_threshold`, `test_feedback.py::test_corrections_default_to_validation_so_the_test_split_stays_frozen` | The `protect-paths` hook guarding gold and reports runs only on an agent's machine; `main` is branch-protected (task 107), but nothing yet enforces that a behaviour change bumps its `*_VERSION` constant, and promotion is not gated on the full evidence set | 507, 1007 | Release owner |
| I5 | **PA-required comes from input**, never inferred from policy text | `test_workflow.py::test_the_pa_trigger_node_says_the_value_was_declared_not_inferred`, `test_api.py::test_creating_a_case_that_infers_pa_required_is_rejected` | The case-creation UI must ask for the declared value rather than default it | 301 | Release owner |
| I6 | **Synthetic data only; PHI-safe logging** | `test_config.py::test_logging_patient_text_is_refused_outside_a_developer_machine`, `test_observability.py::test_a_whole_case_run_emits_no_patient_text`, `test_observability.py::test_a_field_outside_the_allow_list_is_dropped_and_the_drop_is_recorded` | Exception text bypasses the allow-list (threat T7); no automated scan keeps real data or secrets out of the repository; synthetic-only is a process control today | 805, 1005, 1101 | Release owner |
| I7 | **Tenant isolation** — another organization's resource is a 404 | `test_api.py::test_the_same_case_id_is_isolated_between_organizations`, `test_api.py::test_a_cited_document_is_a_404_from_another_organization`, `test_persistence.py::test_runs_and_decisions_are_invisible_across_organizations`, `test_jobs.py::test_job_reads_are_scoped_to_the_verified_organization` | Isolation is proven per layer, not through UI, proxy, API, jobs and storage together; no database-level backstop (threat T3) | 205, 1001, 1006 | Release owner |
| I8 | **Version what produces a result** | `test_workflow.py::test_nodes_record_the_component_versions_their_output_depends_on`, `test_persistence.py::test_versions_are_columns_so_a_bump_can_be_compared`, `test_feedback.py::test_a_decision_copies_the_answer_and_versions_it_was_filed_against`, `test_case_assembly.py::test_a_run_records_the_exact_policy_source_it_was_evaluated_against` (task 601) | Nothing fails when behaviour changes without a version bump; a run records its policy source digest but not yet the classifier artifact and image it ran on (threat T15) | 507, 605 | Release owner |
| I9 | **Offline core** — the service layer wraps the workflow, never the reverse | `test_scope_boundaries.py::test_the_core_never_imports_the_service_layer`, `test_scope_boundaries.py::test_the_core_imports_no_network_module`, `test_scope_boundaries.py::test_the_core_imports_only_the_standard_library_and_declared_core_dependencies` | None known. Re-confirm at release that the closure still covers every workflow stage | 1007 | Release owner |
| I10 | **Deployed environments fail closed** — auth on, S3, strict origins, compliance-mode Object Lock | `test_api.py::test_the_api_refuses_to_start_unauthenticated_outside_local`, `test_config.py::test_deployed_environments_require_complete_oidc_configuration`, `test_config.py::test_deployed_environments_require_postgresql`, `test_config.py::test_a_plain_http_origin_is_refused_in_a_deployed_environment`, `test_config.py::test_production_requires_compliance_object_lock_mode` | The UI server does not fail closed: it can run deployed with a shared token or a local principal (threat T1) | 204, 703, 704 | Release owner |

## Workstreams

Each backlog epic is one workstream. **Accepted when** is the evidence the release owner must see
before the epic is closed. **Open tasks** are the tasks whose evidence it waits on; the full child
list is in the backlog.

| Epic | Workstream | Target | Accepted when | Evidence today | Open tasks | Owner |
|---|---|---|---|---|---|---|
| 100 | Scope and release gates | V1 | Scope ADR accepted; threat model baselined; this checklist approved; owners and measurable objectives agreed; retention policy approved before Object Lock is provisioned; AWS architecture recorded; `main` protection recorded | ADR 002 (task 101), accepted service objectives and ownership (task 102), this checklist approved (task 103), threat model (task 104), `main` branch protection confirmed by the release owner (task 107), `test_scope_boundaries.py::test_the_closure_covers_every_workflow_stage`, `test_doc_references.py::test_every_cited_test_exists` | 105, 106 | Release owner |
| 200 | Reviewer identity and access | V1 | Each reviewer signs in through OIDC with a distinct verified subject; sessions refresh and revoke server-side; the deployed UI refuses unsafe auth; cross-org access is a 404 through the full stack; key rotation and offboarding are tested and documented | `test_auth.py::test_jwt_authenticator_maps_verified_subject_organization_and_roles`, `test_auth.py::test_jwt_authenticator_returns_one_stable_error_for_invalid_tokens`, `test_api.py::test_roles_are_enforced_before_a_case_is_created` | 201, 202, 203, 204, 205, 206, 207 | Release owner |
| 300 | Reviewer workflow completion | V1 | A reviewer can create a case, upload, follow the job, open exact page and policy citations, correct and escalate, with accessible and concurrency-safe forms | `test_api.py::test_the_worklist_pages_and_states_how_many_there_are`, `test_api.py::test_a_reviewer_decision_is_recorded_against_a_stored_run`, `test_persistence.py::test_a_superseding_decision_is_another_row_not_an_edit`, vitest suite in `web/` | 301, 302, 303, 304, 305, 306, 307, 308, 309 | Release owner |
| 400 | Upload and processing security | V1 | Uploads are quarantined, scanned and released before any parser or reviewer reads them; parsers are resource-isolated; quotas apply; browser headers are hardened; orphans are reconciled | `test_uploads.py::test_an_extension_cannot_disguise_another_file_type`, `test_uploads.py::test_the_stream_stops_at_the_file_limit_and_removes_its_temporary_file`, `test_api.py::test_the_request_body_limit_rejects_before_multipart_staging`, `test_api.py::test_a_served_document_is_an_attachment_that_cannot_be_sniffed` | 401, 402, 403, 404, 405, 406, 407 | Release owner |
| 500 | Pipeline evaluation and quality | V1 | OCR is shipped and measured; independent challenge sets score full cases across formats; routing thresholds are validated; a capability and limitation matrix states what was measured on synthetic data only | `rxauth-evaluate` and `rxauth-check-reports` in CI, `test_evaluation_suite.py::test_generation_is_gated_on_producing_no_unsupported_claims` | 501, 502, 503, 504, 505, 506, 507, 508, 509 | Release owner |
| 600 | Policy and model lifecycle | V1 | Policy sources are immutable with checksums and effective dates; publication rejects ambiguous windows; updates and rollbacks are reviewed; a fresh deployment loads a verified classifier; every run records full provenance | `test_policy_retrieval.py::test_overlapping_version_windows_are_refused_rather_than_ranked`, `test_policy_retrieval.py::test_another_payers_near_identical_policy_is_never_returned`, `test_registry.py::test_a_tampered_weights_file_is_refused`; task 601: `test_policy_corpus.py::test_a_published_policy_whose_bytes_change_is_refused`, `test_policy_corpus.py::test_the_pa_207_retrieval_trap_is_published_unchanged`, `test_policy_corpus.py::test_a_recorded_digest_resolves_to_the_original_source`, `rxauth-publish-policies --check` in the `reports` CI job | 602, 603, 604, 605, 606 | Release owner |
| 700 | AWS deployment and delivery | V1 | Isolated environments from infrastructure as code; private networking, TLS, least-privilege IAM, managed secrets; Object Lock provisioned after retention approval; reproducible images promoted immutably; migrations, probes, draining, autoscaling, edge protection and DNS in place | `test_api.py::test_s3_uploads_are_server_side_encrypted`, `test_api.py::test_s3_uploads_apply_the_configured_object_retention`, `postgres` CI job (migrations up, check, down) | 701, 702, 703, 704, 705, 706, 707, 708, 709, 710, 711, 712 | Release owner |
| 800 | Observability and operations | V1 | Safe metrics, dashboards and paging alerts; one correlation id from UI to storage; protected audit events; safe logs verified on failure paths; runbooks; measured capacity and cost | `test_observability.py::test_a_run_context_correlates_every_line_of_one_run`, `test_observability.py::test_every_node_logs_its_stage_latency_and_correlation_ids` | 801, 802, 803, 804, 805, 806, 807 | Release owner |
| 900 | Durability, performance and recovery | V1 | Multi-worker crash durability, dependency-interruption recovery, load and soak results, encrypted backups, a rehearsed restore, consistent retention cleanup, idempotent retries and tenant offboarding | `test_jobs.py::test_a_live_lease_prevents_claiming_and_an_expired_lease_is_recovered`, `test_jobs.py::test_a_new_queue_instance_processes_a_job_created_before_restart`, `test_api.py::test_case_documents_and_an_accepted_job_survive_an_api_restart` | 901, 902, 903, 904, 905, 906, 907, 908, 909, 910 | Release owner |
| 1000 | Integration and security assurance | V1 | Browser end-to-end tests in CI; AWS storage and scanning integration tests; API and TypeScript contract checks; dependency, secret and image scans with an SBOM; an independent penetration assessment; release gates that require all of it | The `test`, `postgres`, `reports` and `web` jobs in `.github/workflows/ci.yml` | 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009 | Release owner |
| 1100 | Commercial pilot prerequisites | Pilot only | Separate authorization, expert privacy, security and contract review, data governance, an incident program, and pilot validation outside the public repository. Never blocks V1 (ADR 002) | ADR 002 | 1101, 1102, 1103, 1104, 1105, 1106, 1107 | Release owner |
| 1200 | Production acceptance and launch | V1 | Every V1 row in this checklist is closed; UAT passed; rollback rehearsed; release decision recorded; staged rollout and post-launch soak complete | This checklist | 1201, 1202, 1203, 1204, 1205, 1206 | Release owner |

## Using it at the release decision

1. For each row, confirm every open task is Done and links its evidence: a test, a command
   output, a report, or a recorded setting.
2. Re-run the CI-backed evidence at the release commit. Evidence from an earlier commit does not
   carry forward.
3. Record acceptance against the release commit in the table below. Commercial-pilot rows (epic
   1100) are excluded from the V1 decision and accepted separately at task 1107.

## Approval

| Role | Name | Decision | Date | Release commit |
|---|---|---|---|---|
| Release owner | Bavely Tawfik | Pending | — | — |
