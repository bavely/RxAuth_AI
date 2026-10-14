# Synthetic V1 service objectives and ownership

**Status:** proposed, 2026-10-13 (RXAUTH-PLAN-102). Pending owner acceptance.
**Scope:** synthetic-only production V1 under [ADR 002](adr-002-production-v1-scope.md).
**Release gate:** [release checklist](release-checklist.md), epic 100 and task 1204.

Task 101 is complete. Task 102 is the next prerequisite for architecture (106),
metrics (801), and load testing (903). This document supplies a reviewable operating
proposal; the numbers below are proposed targets, not measured baselines or promises.
No production performance, availability, restore, or cost baseline has been measured here.

## Ownership

ADR 002 records Bavely Tawfik as release owner. That existing accountability carries
forward; the following workstream assignments remain unassigned until accepted.
The release owner must name a person and backup for each role before task 102 is Done.

| Role | Responsibilities | Assigned person / backup | Acceptance |
|---|---|---|---|
| Release owner | Approve targets, evidence, exceptions and release/stop decisions | Bavely Tawfik / pending | Existing role; this proposal pending |
| Service operations owner | Availability, capacity, queue age, paging and incident response | Pending / pending | Pending |
| Recovery owner | Database and object restore, consistency, rollback rehearsal | Pending / pending | Pending |
| Cost owner | Budget, usage allocation, retention forecast and spend alerts | Pending / pending | Pending |

One person may accept multiple roles. An assignment does not imply staffed 24-hour
coverage; record coverage hours and escalation contacts in task 806's runbook.

## Proposed targets and measurement contract

Measure staging first, then production separately. Use UTC timestamps and record the
release digest, settings, environment, workload mix, sample count and observation window.
Never combine laptop timing with deployed-service timing. Missing samples are gaps,
not passing measurements. Keep identifiers out of metric labels and export no source text.

| Objective | Proposed target | Measurement and acceptance evidence | Accountable role | Delivery tasks |
|---|---|---|---|---|
| Reviewer-path availability | At least 99.5% over a rolling 30 days | Every minute, a dedicated synthetic tenant signs in and reads a worklist and a known citation through UI/API. Success requires expected content within 10 seconds. Successful scheduled probes / all scheduled probes; missing probes count as unavailable. Include maintenance. Report shorter staging windows explicitly; do not call them a 30-day baseline. | Service operations owner | 708, 801, 802, 1001, 1206 |
| Case volume | 100 completed synthetic cases per day; sustain a burst of 10 enqueues per minute for 10 minutes | Run a fixed independently authored mixed-format corpus with declared file/page distributions and concurrency. Count completed runs, terminal failures, duplicates and rejected requests separately. Require at least 99% successful processing of valid supported cases and zero duplicate persisted runs. Repeat across a 24-hour soak; test configured maximum limits separately. | Service operations owner | 504, 901, 903 |
| End-to-end processing latency | p95 at most 5 minutes; p99 at most 15 minutes for successful mixed-format cases | Time from durable enqueue to persisted report available for human review, including queue wait, retries and hydration. Report p50/p95/p99 with sample count, workload strata, and terminal failures beside the percentiles; never count human review time. Collect enough samples to interpret p99 and disclose the percentile method. | Service operations owner | 801, 903, 904 |
| Queue age | Oldest eligible queued job at most 2 minutes under the declared load; alert after 5 consecutive minutes above 2 minutes | Sample once per minute. Eligible means queued/retrying and next_attempt_at at or before now. Age is now minus next_attempt_at. Also report oldest enqueue age, delayed-retry backlog and running-job age so backoff or stuck execution cannot disappear from the dashboard. | Service operations owner | 801, 802, 903, 904 |
| Full-environment recovery | RTO at most 4 hours; RPO at most 1 hour | Start RTO at declared service interruption, end when identity, upload, queue, citations and review attribution pass in the restored environment. Restore database and exact S3 object versions together. Calculate RPO from the newest recoverable consistent checkpoint relative to interruption; enumerate lost synthetic transactions and verify tenant isolation, no duplicate runs and citation hashes. Record actual times and gaps. | Recovery owner | 906, 907, 908, 1203 |
| Cost | At most USD 500 per month per synthetic production environment | Cost owner reviews actual AWS billing and usage by environment: compute, PostgreSQL, storage, retained versions, backups, scanning, monitoring, networking and egress. Report absolute spend plus cost per completed case with case count; zero cases means undefined unit cost. Forecast retained-storage obligations separately from the monthly bill. Alert at 80% of approved budget. | Cost owner | 704, 807, 908 |

The load and budget proposals are starting points for owner review. Task 106 must
check whether an architecture can meet both; adjust the proposal through an explicit
decision rather than claim unmeasured feasibility. Maximum-size packets are a separate
capacity and parser-safety test, not assumed to meet the mixed-format latency target.

## Current evidence and known conflicts

`test_observability.py::test_every_node_logs_its_stage_latency_and_correlation_ids`
verifies stage timing, not end-to-end service availability or a production SLO.
`test_jobs.py::test_failed_jobs_retry_three_times_with_exponential_full_jitter` and
`test_jobs.py::test_a_live_lease_prevents_claiming_and_an_expired_lease_is_recovered`
verify queue behavior, not measured recovery time.

Job timestamps are available in `src/rxauth_ai/jobs.py`. Claiming overwrites
`started_at`, so finished_at minus started_at is only the final attempt duration;
finished_at minus created_at is the job's end-to-end duration. Task 801 must retain
attempt telemetry for retry analysis and measure report visibility explicitly.

[Current operating settings](production-hardening.md) allow retry delays up to 30
and 60 minutes, use a 15-minute lease, and impose no hard attempt timeout. Failed or
abandoned attempts can exceed the proposed latency targets. Task 904 must reconcile
these settings with the accepted objectives and define stalled-job escalation;
this document changes no settings. RPO also needs database/object consistency proof,
not merely enabled backups. Task 105 must approve retention before task 704 creates
irreversible Object Lock obligations; the proposed budget is not that approval.

## Acceptance and breach response

To complete task 102, record named owners and backups, coverage/escalation, accepted
targets or explicit revisions, and the release owner's decision with its actual date.
Each objective must retain a measurement method and delivery task. Measurement and
operational verification remain gates in their respective tasks and task 1204;
accepting a target does not establish that the service meets it.

During staging and rollout, the operations owner investigates sustained breaches,
records workload and release evidence, and escalates to the release owner. Pause
promotion when an accepted objective fails or evidence is missing; use the rehearsed
rollback/runbook for live incidents. Never automatically resubmit or decide a case.

| Decision | Person | Date | Evidence |
|---|---|---|---|
| Proposed objectives and role assignments | Pending | Pending | This document; no acceptance recorded |

Task 102 remains open until acceptance is recorded. Tasks 106, 801 and 903 must use
the accepted revision of this contract, rather than treating these draft values as approved.
