# Threat model and data-flow inventory

**Status:** baseline, 2026-10-02 (backlog task 104), reviewed against `main@bee2160` plus the
`dev` working tree
**Context:** [ADR 002](adr-002-production-v1-scope.md), README §3 and §19,
[authentication.md](authentication.md), [production-hardening.md](production-hardening.md),
[reviewer-api.md](reviewer-api.md), [reviewer-ui.md](reviewer-ui.md)

## Scope and method

This baseline is a static review of the code, configuration, and tests. It is not a penetration
test: that is task 1006, and nothing here claims an exploit was observed. Every control below
names the code that implements it and, where one exists, the test that proves it. Every gap
names the backlog task in [jira-production-readiness.csv](jira-production-readiness.csv) that
closes it.

ADR 002 makes production V1 synthetic-only, but it also requires `environment=production` to
behave as though it held PHI. This model applies that rule: **every patient-shaped datum is
classified as PHI**, whether or not it is synthetic.

Revisit this document when a trust boundary moves: a new component, a new external service, a
new endpoint class, the identity provider wiring (task 202), the scanning lifecycle (task 401),
or the AWS architecture decision (task 106).

## Components and trust boundaries

| # | Component | Runs as | Trusts | Holds |
|---|---|---|---|---|
| C1 | Reviewer browser | the reviewer's device | nothing it receives without TLS | rendered HTML, one document at a time |
| C2 | UI server (Next.js, `web/`) | `node` user, container | the API's responses; its own environment | the API token, when one is configured |
| C3 | API (FastAPI, `api.py`) | `rxauth` user, container | verified JWT claims only | request bodies, staged uploads |
| C4 | Worker (`worker.py`, `case_jobs.py`) | `rxauth` user, container | job rows, stored SHA-256 digests | every tenant's scratch copies, during processing |
| C5 | Migration task (`alembic`) | one-shot container | the migration scripts | schema-owner database credentials |
| C6 | PostgreSQL | managed service | its clients' credentials | cases, document metadata, jobs, runs, decisions |
| C7 | S3 bucket | managed service | IAM | original documents under Object Lock |
| C8 | Identity provider (OIDC) | external | its own administrators | users, roles, the organization claim |
| C9 | Log and telemetry sink | external | the emitting process | structured log lines |
| C10 | Scratch disk (`artifacts/cases/<org>/`) | worker filesystem | the worker | hydrated documents for up to 72 hours |
| C11 | Policy corpus and classifier artifact | baked into the API/worker image | the image build | public policies, model weights |

The trust boundaries a request crosses, in order: **B1** browser → UI server (public network),
**B2** UI server → API (private network), **B3** API → identity provider's JWKS endpoint,
**B4** API and worker → PostgreSQL, **B5** API and worker → S3, **B6** worker → document
parsers (pypdf, OpenCV, Tesseract) running on untrusted bytes, **B7** every process → log sink.

## Data classification

| Class | Examples | Where it lives |
|---|---|---|
| **PHI** | uploaded documents, page text, quoted evidence spans, case runs, checklists, reviewer decisions and notes | S3 (C7), PostgreSQL (C6), scratch (C10), UI server memory while rendering (C2) |
| **Tenant identifiers** | `organization_id`, `case_id`, `document_id`, `run_id`, job id | everywhere; logged by design |
| **Credentials** | access tokens, database URL with password, AWS credentials | process memory and environment; never in settings fields for AWS (`storage.py`) |
| **Public** | payer policies, model weights, reports | image (C11), repository |

Filenames are PHI. A synthetic filename can still be shaped like a real one
(`jane_doe_mri.pdf`), and `observability.LOGGABLE_FIELDS` deliberately excludes `filename`.

## Data-flow inventory

| Flow | From → to | Data | Protection today | Evidence |
|---|---|---|---|---|
| F1 | C1 → C2 | page requests, decision form posts | HTTPS assumed at the edge (not yet provisioned, task 712); Next.js Server Actions check `Origin` against `Host` | none in this repository (tasks 202, 1006) |
| F2 | C2 → C1 | rendered pages, cited document bytes | `no-store`, `nosniff`, `no-referrer`, `noindex`; document disposition preserved | `web/next.config.ts`, `web/src/app/api/documents/` |
| F3 | C2 → C3 | API calls with bearer token | server-only modules; the browser never sees a token | `web/src/lib/auth.ts` and `api.ts` import `server-only` |
| F4 | C3 → C8 | JWKS fetch | asymmetric algorithms only, fixed by configuration | `test_config.py::test_authentication_accepts_only_configured_asymmetric_algorithms` |
| F5 | C3 → C7 | uploaded document | streamed, size-bounded, signature-checked, SHA-256 recorded, SSE-AES256, Object Lock retain-until | `test_uploads.py::test_an_extension_cannot_disguise_another_file_type`, `test_api.py::test_s3_uploads_are_server_side_encrypted`, `test_api.py::test_s3_uploads_apply_the_configured_object_retention` |
| F6 | C3 ↔ C6 | case, document, run, decision rows | every query filtered by the principal's organization | `test_persistence.py::test_runs_and_decisions_are_invisible_across_organizations` |
| F7 | C3 → C6 → C4 | job enqueue and claim | transactional enqueue; `FOR UPDATE SKIP LOCKED`; lease heartbeat | `test_jobs.py::test_a_live_lease_prevents_claiming_and_an_expired_lease_is_recovered` |
| F8 | C7 → C4 → C10 | hydrated documents | per-tenant directory; every object re-verified against its stored SHA-256 | `case_jobs.py` hydration |
| F9 | C10 → C4 (parsers) | untrusted bytes into pypdf, OpenCV, Tesseract | pixel and page limits from upload validation; **no sandbox** | task 404 |
| F10 | C4 → C6 | case run with every cited span | organization-scoped write; run id equals job id, so a retry cannot duplicate it | `production-hardening.md` |
| F11 | C7 → C3 → C2 → C1 | cited document bytes | organization-scoped lookup; digest verified before serving; `no-store` | `test_api.py::test_a_stored_object_that_no_longer_matches_its_digest_is_refused`, `test_api.py::test_a_cited_document_is_a_404_from_another_organization` |
| F12 | C2–C5 → C9 | structured logs | allow-listed fields; source text only when `environment=local` | `test_observability.py::test_a_whole_case_run_emits_no_patient_text`, `test_config.py::test_logging_patient_text_is_refused_outside_a_developer_machine` |
| F13 | build → C11 | policies, classifier weights | artifact digests verified on load; pickle-free format | `test_registry.py::test_a_tampered_weights_file_is_refused` |

## Controls already in place

- **Fail-closed configuration.** Staging and production refuse to start without
  authentication, with local storage, with wildcard or plain-HTTP origins, or with source-text
  logging (`config.py`; `test_api.py::test_the_api_refuses_to_start_unauthenticated_outside_local`,
  `test_config.py::test_a_wildcard_origin_is_refused_in_a_deployed_environment`).
- **Identity comes from the token.** Organization and roles are read from verified claims, never
  from the request. Invalid tokens all get one stable error, so the response does not reveal why
  validation failed (`test_auth.py::test_jwt_authenticator_returns_one_stable_error_for_invalid_tokens`).
- **Tenant isolation answers 404.** Another organization's case, job, run, or document is
  indistinguishable from one that does not exist
  (`test_api.py::test_the_same_case_id_is_isolated_between_organizations`,
  `test_jobs.py::test_job_reads_are_scoped_to_the_verified_organization`).
- **Paths cannot escape.** Organization identifiers are pattern-restricted, and filenames are
  reduced to their base name (`test_auth.py::test_principal_rejects_an_organization_identifier_that_can_traverse_paths`,
  `test_api.py::test_a_filename_with_a_path_in_it_is_reduced_to_its_name`).
- **Upload bounds come before parsing.** The request body is limited before multipart staging,
  and per-case count and byte limits apply (`test_api.py::test_the_request_body_limit_rejects_before_multipart_staging`).
- **Integrity end to end.** SHA-256 is recorded on upload and re-checked on hydration and on
  serving. Model artifacts are digest-checked, and none is unpickled.
- **Containers run as non-root** (`USER rxauth`, `USER node`).

## Threats and attack paths

Severity reflects the deployed V1 configuration if it shipped today with the stated gap open.
**Open** means no control exists. **Partial** means a control exists but does not cover the whole path.

| # | Threat (STRIDE) | Attack path | Status | Closed by |
|---|---|---|---|---|
| T1 | Spoofing, elevation | **The UI server has no sign-in.** With `RXAUTH_API_TOKEN` set, anyone who can reach the UI reads every case, opens every document, and records decisions as the token's subject, across that token's whole organization. Without it, a deployed API answers 401 to everything, which is safe but unusable | **Open, high** | 202, 203, 204 |
| T2 | Repudiation | A shared static token records every decision against one service subject, so §16 attribution is lost | **Open, high** | 202 |
| T3 | Information disclosure | A new query that forgets its `organization_id` filter leaks across tenants. Isolation is enforced only in the application, with no database-level backstop such as row-level security | **Partial** (every current path is tested) | 205 |
| T4 | Elevation | The tenant boundary is the IdP's organization claim. An IdP administrator, or a misconfigured claim mapping, can place a user in any tenant | **Partial** (claim name is fixed by configuration) | 201, 207 |
| T5 | Tampering, elevation | A crafted PDF or image exploits pypdf, OpenCV, or Tesseract inside the worker. That one process holds every tenant's scratch copies and bucket read access | **Open, high** for real data | 401, 402, 403, 404 |
| T6 | Tampering | Malware is stored and served back to reviewers. Signature checks are not malware detection | **Open** (accepted for synthetic V1 only) | 401, 402, 403 |
| T7 | Information disclosure | **Exception text bypasses the logging allow-list.** `workflow.py`, `observability.timed`, and `jobs.py` write `str(exc)` to the allow-listed `error` field, which is not among the `log_source_text`-gated `PHI_BEARING_FIELDS`. The same string is stored in the failed node's record inside the saved run, and in the persisted job that `GET /jobs/{id}` returns. A pydantic `ValidationError` includes the rejected input by default, so a span or page fragment can reach logs and job rows outside `environment=local`. `test_a_whole_case_run_emits_no_patient_text` covers only a successful run | **Open** | 805 |
| T8 | Information disclosure | Browser-facing responses lack CSP, framing protection, and HSTS. When the API is unreachable, the document route returns the raw error message to the browser, and that message contains the internal API URL | **Partial** | 406 |
| T9 | Information disclosure | Unauthenticated `GET /health` reveals the environment name, whether a database is configured, and the storage backend | **Low** | 708 |
| T10 | Denial of service | A tenant or anonymous client floods uploads or runs. Per-request limits exist; per-tenant quotas, rate limits, and edge protection do not | **Partial** | 405, 711 |
| T11 | Repudiation | Access, upload, run, and decision events go to operational logs only. No append-only, access-controlled audit store exists | **Open** | 804 |
| T12 | Tampering (supply chain) | The API image copies `ghcr.io/astral-sh/uv:latest`, unpinned, and no dependency, secret, or image scan runs in CI | **Open** | 705, 1005, 1008 |
| T13 | Information disclosure | Credentials: compose uses a fixed local database password, acceptable only because compose is `environment=local`. Deployed secret storage and rotation do not exist yet | **Open** for deployment | 703 |
| T14 | Information disclosure | Backups and restored environments carry PHI-class data outside the primary controls | **Open** | 906, 907 |
| T15 | Tampering | Policy versions are files baked into the image. Since task 601 each run stamps the SHA-256 of the exact policy source it used, and a corpus that differs from its publication manifest refuses to load (`test_policy_corpus.py::test_a_published_policy_whose_bytes_change_is_refused`). The manifest is not signed, so whoever can change the image can republish it, and a run does not yet record the image or classifier artifact it ran on | **Partial** (policy sources are stamped and verified; release provenance is not) | 605 |
| T16 | Denial of service, legal | Object Lock in `COMPLIANCE` mode is irreversible. Provisioning it before the retention policy is approved would make that policy unchangeable | **Process risk** | 105, 704 |
| T17 | Information disclosure | A CSRF-style forged decision post. Next.js Server Actions reject a mismatched `Origin`, and the API takes the reviewer from the token. This needs re-verification once session cookies exist (task 202) | **Mitigated by framework, unverified** | 203, 1006 |

## Tenant attack paths, summarized

An attacker authenticated to organization A, attacking organization B:

1. **Guess identifiers** (case, job, run, document): 404, indistinguishable from absent. *Mitigated.*
2. **Traverse paths** through `organization_id`, `case_id`, or filenames: rejected by pattern or
   reduced to a base name. *Mitigated.*
3. **Forge the organization claim**: requires a token the IdP signed. *Mitigated in the API;
   depends on IdP administration (T4).*
4. **Exploit a parser** with a crafted upload to read B's scratch copies or use the worker's
   bucket access: *open* (T5) until scanning and parser isolation land.
5. **Use a shared UI deployment**: if A and B are served by one UI with one static token, they are
   the same tenant to the API. *Open* (T1) until per-reviewer sign-in exists.

## Out of scope for this baseline

Clinical safety (the system makes no clinical decision, ADR 002); the commercial HIPAA program
(epic 1100); physical and cloud-provider controls (the AWS shared-responsibility model applies
once task 106 picks services).
