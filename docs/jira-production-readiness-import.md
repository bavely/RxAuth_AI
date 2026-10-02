# Production-readiness backlog and Jira import

Assessment date: 2026-10-02. Import file: [jira-production-readiness.csv](jira-production-readiness.csv).

The backlog contains **101 work items: 12 epics and 89 tasks**. Seven tasks under
Commercial pilot prerequisites apply only to a separately authorized real-data
deployment. The other 82 tasks cover completing and validating synthetic-only
production V1. This is a remaining-work backlog, not a list of previously completed
milestones. Review and split tasks during team estimation; no staffing, estimates,
deadlines or live Jira project configuration were supplied.

## Assessment basis

Started with AGENTS.md and the first 125 lines of docs/code-walkthrough.md, then
reviewed README.md, authentication, reviewer API/UI and production-hardening guides,
the CI workflow, packaging, Docker/Compose, evaluation reports, and relevant auth,
configuration, API health, artifact, feedback and worker code. The source and test
inventory informed likely change surfaces. This was static analysis, not a full
line-by-line security audit or an executed application test suite.

Existing foundations include the deterministic 13-node pipeline, citation gates,
five-state matching, policy-version selection, evaluated classifier, authenticated
tenant-scoped API, Postgres/Alembic, durable jobs, validated uploads, S3 retention
controls and reviewer UI. The backlog completes or verifies those capabilities;
it does not ask to rebuild them.

Explicit gaps include the throwing OIDC session-provider stub, unmeasured OCR in
the committed ingestion report, missing page-text/policy-context/upload UI features,
deferred malware scanning, and absent browser end-to-end tests. Fresh deployment
also needs an OCR Python backend and verified classifier artifact; a Tesseract
binary or an empty artifacts volume alone does not supply these. Other hardening
items are proposed production controls or verification work, not confirmed defects.
Documentation includes stale thread-pool and absent-UI descriptions, so code and
newer guides take precedence over those historical statements.

P0 defines scope and objectives; P1 establishes secure identity, artifacts and
infrastructure; P2 completes reviewer flows and validation; P3 proves operations
and recovery; P4 gates launch. These are suggested planning phases, not existing
repository milestone numbers. Dependencies control sequencing across phases.

Production here means an operable, secured, validated **synthetic-data system**.
Accepting real patient documents requires a separate organizational authorization,
qualified privacy/security review, contractual prerequisites and controlled pilot
validation. No backlog entry certifies legal compliance or clinical generalization.
Keep PHI outside this public repository and its tests, gold sets and CI.

## Import steps

Use Jira's CSV importer supporting work types and hierarchy, preferably an
administrator-managed import into a sandbox first. Select your actual target
project in the wizard. Do not assign the CSV-local IDs as existing Jira issue keys.
Parents appear before their children.

| CSV column | Jira mapping |
|---|---|
| Issue ID | Work item ID / Issue ID, used for import relationship resolution |
| Issue Type | Work type / Issue Type; map Epic and Task to your project's equivalents |
| Summary | Summary, required |
| Description | Description; includes acceptance criteria, dependencies, scope and evidence |
| Priority | Priority; map High and Highest to supported values |
| Labels | Labels |
| Parent | Parent, referring to the CSV-local epic ID |

The file uses UTF-8, commas, quoted fields, CRLF row endings and multiline
descriptions. Choose UTF-8 and comma delimiters. Leave workflow status at the
project's default initial state. Configure any additional fields required by your
project during import; assignee, reporter, dates and estimates are intentionally
absent. This avoids assigning people or claiming work is already done.

Current Jira Cloud supports work-item ID and Parent mappings for CSV hierarchy.
See Atlassian's [CSV preparation guide](https://support.atlassian.com/jira-software-cloud/docs/prepare-a-csv-file-for-import/)
and [parent-child mapping guidance](https://support.atlassian.com/jira/kb/keep-issue-parent-child-mapping-during-csv-import-to-jira-cloud/).
Older or customized Jira importers may require Epic Name/Epic Link and a two-pass
import using generated epic keys. If Parent is unavailable, retain the description
and labels, import epics first, and adapt child relationships to the supported
mapping. A CSV cannot guarantee compatibility with an unspecified Jira edition,
permission model, work-type scheme or custom required fields.

Dependencies are deliberately embedded in Description as numeric CSV-local IDs,
not automatically imported issue links. For example, `202` means the task with
Issue ID 202, also identified as RXAUTH-PLAN-202 in its description. After import,
map those IDs to created Jira keys and add `is blocked by` links as needed. Parent
relationships organize tasks into epics; they do not represent dependency links.
See [Atlassian's field-mapping guidance](https://support.atlassian.com/jira-software-cloud/docs/mapping-csv-data-to-jira-fields)
for importers that support configured issue-link columns.

Validate the preview, map priority/type values, import, then verify 12 epics and
89 tasks, correct parent relationships, multiline descriptions and the separate
`rxauth-commercial-gate` label. Record the resulting Jira keys. Reimporting this
creation CSV can create duplicates; it contains no existing issue keys for updates.

## Artifact verification

The CSV was parsed back using Python's csv module and checked for exact round-trip
content, unique IDs, valid parent references, valid dependency references, absence
of dependency cycles, and summaries within 255 characters. Application tests and
report regeneration were unnecessary for this documentation-only change and were
not run. No application behavior, version constants, reports or gold sets changed.
