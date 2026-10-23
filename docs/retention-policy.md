# Synthetic V1 retention and legal-hold policy

**Status:** accepted, 2026-10-22 (RXAUTH-PLAN-105). Approved as a whole by the release owner.
**Scope:** synthetic-only production V1 under [ADR 002](adr-002-production-v1-scope.md). Not a
compliance claim, and not the commercial pilot's policy.
**Release gate:** [release checklist](release-checklist.md), epic 100; must be accepted before
task 704 provisions any Object Lock bucket.

Task 105 exists because of threat-model T16: COMPLIANCE-mode Object Lock cannot be shortened or
bypassed by anyone, including the AWS root user. A retention period written into a bucket before
it is agreed becomes the policy whether or not anyone agreed to it. This document records what
every class of data is kept for, why, and which task enforces it, so 704 provisions an approved
value instead of a code default.

## Decisions

**Documents are locked for 1 year in synthetic V1.** COMPLIANCE mode stays on, so the
irreversible control is exercised exactly as production requires (`config.py` refuses any other
mode in production), but synthetic test data becomes deletable after a year instead of a decade.
The code default of 10 years is deliberately unchanged: it is the conservative value a real-data
deployment starts from, and task 1103 decides the commercial value with qualified review. Synthetic
V1 sets the deployed value through `RXAUTH_ORIGINAL_DOCUMENT_RETENTION_YEARS=1`.

**Run records live exactly as long as the documents they cite.** A run's citations are character
spans into specific document bytes. A run kept after those bytes expire holds citations nobody can
verify, which is the failure this project refuses everywhere else (AGENTS.md invariant 3). A run
purged earlier loses the review history attached to it. So a run, its criterion evaluations and
its reviewer decisions share one expiry: the latest `retain_until` of the documents the run used.
A decision recorded late in that window still expires with its run.

**Legal hold is an S3 Object Lock legal hold plus a database hold flag.** S3's per-object legal
hold blocks deletion independently of retain-until, so it covers bytes after their lock expires.
A hold flag on the organization or case covers everything S3 does not hold: run, evaluation,
decision and job rows, temporary copies, and the purge of expired objects. Every purge path must
skip held data. Only the release owner places or lifts a hold, and each change is an audit event
(task 804).

## Retention by data class

Periods run from the creation of the record unless the table says otherwise. "Current" is what
the code enforces today; "V1 deployed" is what synthetic production runs with.

| Data | Current | V1 deployed | Enforcement | Status | Task |
|---|---|---|---|---|---|
| Released original documents (S3, all versions) | 10 calendar years, COMPLIANCE | **1 calendar year, COMPLIANCE** | Object Lock retain-until on upload; `uploaded_documents.retain_until` | Accepted | 704 |
| Uploaded-document metadata rows | never purged | Same expiry as the object | New purge, after the object is deletable | Accepted | 908 |
| Runs, criterion evaluations, reviewer decisions | never purged | **Latest `retain_until` of the run's documents** | New purge, all three together | Accepted | 908 |
| Case rows | never purged | When the case has no remaining documents or runs | New purge | Accepted | 908 |
| Successful job rows | 6 calendar years | 1 calendar year (`RXAUTH_COMPLETED_JOB_RETENTION_YEARS=1`) | `expires_at`; hourly worker purge | Accepted | 908 |
| Terminal failed job rows | 90 days | 90 days | `expires_at`; hourly worker purge | Accepted (unchanged) | 908 |
| Temporary processing copies | 72 hours | 72 hours | Hourly worker maintenance | Accepted (unchanged) | 703, 908 |
| Quarantined uploads awaiting a scan verdict | not built | 7 days, then deleted as failed | Quarantine lifecycle | Accepted | 401, 407 |
| Rejected uploads | not built | Bytes deleted within 24 hours of the verdict; hash, verdict and metadata kept with the case | Quarantine lifecycle | Accepted | 401, 402 |
| Database backups and point-in-time recovery | none | 35 days | Managed PostgreSQL automated backups | Accepted | 906 |
| Application logs and telemetry | none defined | 90 days | Log sink retention setting | Accepted | 801, 805 |
| Security audit events | not built | 2 years, append-only | Protected audit store | Accepted | 804 |

Two consequences follow from the table and bind the tasks that implement it:

- **Object Lock applies at release, not at upload.** A malicious file that fails scanning must
  be deletable, and a locked object cannot be deleted for a year. Quarantine therefore lives
  somewhere without Object Lock (a separate bucket or a lock-free prefix), and the lock and its
  retain-until are applied when the scanned bytes are released. Task 401 designs this; today's
  upload path locks immediately, which is acceptable only until scanning exists.
- **Deletion is complete only when backups age out.** A purged row survives in backups for up
  to 35 days. A restore (task 907) must re-apply purges and legal holds before it serves traffic,
  so a restored environment never resurrects expired data or drops a hold (task 908).

## Legal hold procedure

1. The release owner records the reason and scope (organization, case, or document) in the
   audit store.
2. S3 legal hold is set on every current and noncurrent version of the affected objects.
3. The database hold flag is set on the organization or case; every purge path checks it.
4. Releasing a hold reverses steps 2 and 3, is audited, and leaves expiry to the normal schedule:
   anything already past its retention is purged on the next maintenance run.

The worker identity never holds `s3:PutObjectLegalHold` or `s3:BypassGovernanceRetention`; no
workload role can lift a hold (task 702).

## Tenant offboarding

Offboarding (task 910) revokes access immediately and exports the tenant's records on request.
Data still under Object Lock or legal hold cannot be deleted. It stays inaccessible to every
principal and is purged by the normal schedule once both have lapsed. The 1-year V1 lock bounds
that wait for synthetic tenants.

## Out of scope

- Repository content: synthetic corpora, gold sets, and committed reports are governed by git
  history and AGENTS.md invariant 4, not by this policy.
- `data/reviewer_feedback.jsonl` and local `artifacts/`: developer-machine runtime output, never
  deployed.
- Real patient data: not accepted in V1. The commercial pilot must re-decide every value above
  under task 1103, including whether 10 years, or another period, applies.

## Implementation backlog this creates

No code changes with this document; it defines what later tasks must build and verify.

- **704:** provision versioning and COMPLIANCE Object Lock with the 1-year value; lifecycle rules
  that expire noncurrent versions only after their lock lapses; deny the legal-hold permission to
  workload roles.
- **908:** purges for document metadata, runs with their evaluations and decisions, and cases; hold
  flag (schema change through `/add-migration`); controlled-clock tests; restore re-applies purges.
- **401/402/407:** quarantine without Object Lock; lock applied at release; 7-day and 24-hour
  quarantine expiries.
- **804, 801/805, 906:** audit, log and backup retention settings as above.
- **910:** offboarding against this policy.

## Acceptance

| Decision | Person | Date | Evidence |
|---|---|---|---|
| 1-year COMPLIANCE document lock for synthetic V1; runs share their documents' expiry; S3 legal hold plus database hold flag | Bavely Tawfik | 2026-10-22 | Release-owner answers to the task 105 decision questions |
| Remaining defaults and the document as a whole | Bavely Tawfik | 2026-10-22 | Release-owner instruction: "Yes i accept" |

Task 105 is complete as an operating decision. Task 704 provisions Object Lock with these values;
the implementing tasks above must change the code defaults or deployed settings to match before
release.
