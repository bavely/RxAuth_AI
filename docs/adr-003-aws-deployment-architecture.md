# ADR 003 — Synthetic V1 runs on ECS Fargate and RDS PostgreSQL in separate AWS accounts

**Status:** accepted, 2026-10-22 — approved as a whole by the release owner (backlog task 106)
**Context:** [ADR 002](adr-002-production-v1-scope.md) (synthetic-only V1),
[service objectives](service-objectives.md) (task 102), [threat model](threat-model.md) (task 104),
[retention policy](retention-policy.md) (task 105); README §20 (no Kubernetes)

## Context

The service already defines its roles: a one-shot `alembic upgrade head`, the FastAPI API, the
durable `rxauth-worker`, and the Next.js reviewer UI. `docker-compose.yml` runs them on one host
against PostgreSQL and local disk. Production needs the same roles on managed infrastructure that
can meet the accepted objectives: 99.5% reviewer-path availability, 100 cases/day with a burst
of 10 enqueues/minute for 10 minutes, p95 ≤ 5 minutes end to end, queue age ≤ 2 minutes,
RTO ≤ 4 hours, RPO ≤ 1 hour, and at most USD 500/month per environment.

Three facts in the code shape the design:

- **The queue is PostgreSQL.** Jobs are rows claimed with `FOR UPDATE SKIP LOCKED` under renewed
  leases. There is no broker to provision, and adding one (SQS, Redis) is out of scope.
- **A worker runs one job at a time.** `JobWorker.run_once` processes a single claimed job; its
  only extra thread renews the lease. Throughput scales with worker *tasks*, not threads.
- **The API reaches the internet once:** it fetches the identity provider's JWKS
  (`auth.py`, cached). Everything else it talks to is PostgreSQL and S3.

## Decision

| Concern | Decision | Made by |
|---|---|---|
| Compute | **Amazon ECS on AWS Fargate.** One ECS service each for `api`, `worker` and `web`, plus a one-off `migrate` task, all from the existing images | Release owner, 2026-10-22 |
| Database | **Amazon RDS for PostgreSQL 16**, small Graviton instance; **Multi-AZ in production, Single-AZ in staging**; 35-day automated backups with point-in-time recovery | Release owner, 2026-10-22 |
| Infrastructure as code | **Terraform**, remote state in S3 with locking, plan reviewed in the PR before apply | Release owner, 2026-10-22 |
| Accounts and region | **AWS Organizations with separate staging and production accounts** (plus the management account), **us-east-1** | Release owner, 2026-10-22 |

Not Kubernetes (README §20), not App Runner (it cannot host the worker or migration task, so it
would mean two platforms), and not EC2 with compose (one host for every role, no independent
scaling, and self-managed OS recovery against a 4-hour RTO).

## Topology

Per environment, in one VPC across two Availability Zones:

| Subnet tier | Contains | Route out |
|---|---|---|
| Public | Internet-facing ALB, one NAT gateway | Internet gateway |
| Private | ECS tasks: `web`, `api`, `worker`, `migrate`; internal ALB for the API | NAT gateway; S3 gateway endpoint |
| Isolated | RDS | None |

- **Only the UI is public.** The browser never calls the API: every API call is made by the
  Next.js server (`web/src/lib/api.ts`, `import "server-only"`), and document downloads go
  through the UI's proxy route. So the API sits behind an **internal** ALB that accepts only the
  `web` security group, `RXAUTH_CORS_ALLOWED_ORIGINS` stays empty in deployed environments, and
  WAF (task 711) fronts a single public entry point.
- **Encrypted transport everywhere.** Public ALB: HTTPS only, ACM certificate (task 712). Internal
  ALB: HTTPS with an ACM public certificate for an internal hostname under the environment's
  domain, which avoids a paid private CA. RDS: `rds.force_ssl=1`, clients use `sslmode=verify-full`
  with the RDS CA bundle.
- **One NAT gateway per environment.** It carries the JWKS fetch, ECR pulls and AWS API calls; S3
  traffic uses the free gateway endpoint. A NAT AZ outage blocks new image pulls and uncached
  JWKS refreshes in the other AZ. That is accepted for V1 against roughly USD 33/month per extra
  gateway, and revisited if measured availability (task 802) shows it matters.
- **Separate workload identities** (task 702). `api`: write and read its organization prefixes in
  the documents bucket and use the KMS key. `worker`: read documents and use the KMS key. `migrate`:
  database only, no S3. `web`: no AWS permissions. No workload role holds
  `s3:PutObjectLegalHold`, `s3:BypassGovernanceRetention` or bucket-policy rights
  ([retention policy](retention-policy.md)).

## Storage and artifacts

- **Documents bucket:** versioning and Object Lock in COMPLIANCE mode with the 1-year V1
  retention (task 704), SSE-KMS with a customer-managed key, public access blocked. Quarantine
  lives without Object Lock, as the retention policy requires (task 401). The candidate scanner
  for task 402 is GuardDuty Malware Protection for S3, decided there.
- **Scratch:** Fargate ephemeral storage, encrypted by the platform, sized in task 404. Losing a
  task loses only hydrated copies, which the next attempt rebuilds and re-verifies by SHA-256.
- **Classifier artifact:** built into the image, or fetched from a versioned artifacts bucket and
  verified against its manifest at startup. Task 604 chooses; either way an absent or corrupt
  artifact fails readiness (task 708), and the run records which one it used (task 605).
- **Images:** ECR with immutable tags; deploys reference digests (task 706).

## Scaling

| Service | Baseline | Scales on | Ceiling | Notes |
|---|---|---|---|---|
| `web` | 2 tasks, 0.25 vCPU / 0.5 GB | CPU | 4 | Two tasks across AZs for availability |
| `api` | 2 tasks, 0.5 vCPU / 1 GB | CPU, request count | 4 | Handlers are sync `def` on FastAPI's threadpool |
| `worker` | 2 tasks, 1 vCPU / 2 GB | **Oldest eligible queue age** (custom metric, task 801) | 10 | One job per task |
| `migrate` | 0 | Run once per release, before services update | 1 | Task 707 |

**The worker ceiling is not yet known to be enough.** Holding queue age under 2 minutes through
the accepted burst needs roughly *10 enqueues/minute × mean case duration in minutes* concurrent
workers. With a 10-task ceiling, that holds only if a mixed-format case averages about a minute
or less. Case duration on Fargate is unmeasured. Task 903 measures it; if it is longer, either
the ceiling rises, with its cost, or the queue-age objective is revised by an explicit decision
(service objectives, "adjust the contract through an explicit decision").

**The database connection budget binds the ceilings.** The sum over services of
*maximum tasks × SQLAlchemy pool size plus overflow*, plus `migrate` and operator headroom, must
stay under the instance's `max_connections`. Task 710 sets pool sizes against the chosen instance
class; the ceilings above are not final until then.

## Recovery

- **Database:** RDS automated backups for 35 days (matching the retention policy) give
  point-in-time recovery with an RPO of minutes, inside the 1-hour objective. Multi-AZ covers an
  instance or AZ failure in production without a restore.
- **Documents:** versioning plus Object Lock means no object version can be deleted while its
  lock lasts. Restoring means restoring the database to a point and resolving its document rows
  against exact S3 version IDs (tasks 906–908).
- **Everything else is rebuilt, not restored:** Terraform recreates the network and services,
  ECR holds the release digests, and the policy corpus ships in the image.
- **Limitation:** this recovers from instance, AZ and operator failures within us-east-1. A full
  region outage is not covered in V1. Cross-region backup copy is a task 906 decision with its own
  cost.

## Cost

These are **approximate us-east-1 on-demand list prices at drafting time, not measurements**.
Verify them with the AWS Pricing Calculator before task 701 applies anything; task 807 replaces
them with billed figures.

| Item | Production (≈ USD/month) | Staging (≈ USD/month) |
|---|---:|---|
| Fargate baseline tasks (x86 prices; ARM64 is about 20% less if task 705 confirms the images build for it) | 125 | 65 (one task per service) |
| Worker burst capacity (assumes about one hour a day at the ceiling) | 15 | 5 |
| RDS (Multi-AZ prod / Single-AZ staging), storage and backups | 55 | 30 |
| Public ALB and internal ALB | 40 | 40 |
| NAT gateway (one) and data | 35 | 35 |
| WAF, CloudWatch logs/metrics/alarms, KMS, Secrets Manager, S3, ECR, Route 53, malware scanning at synthetic volumes | 50 | 35 |
| **Total** | **≈ 320** | **≈ 210** |

Both sit under the accepted USD 500/month per environment, with room for the worker ceiling to
rise if task 903 needs it. The cost owner sets an AWS Budgets alert at 80% of USD 500 per account
(service objectives).

## Consequences

- Task 701 writes Terraform modules for the account baseline, network, RDS, buckets, ECR, ECS
  services and ALBs, applied from CI with no console-only steps.
- The deployed API is not internet-reachable. A future machine client would need an explicit
  decision to expose it, with its own WAF and rate-limit rules.
- The queue stays in PostgreSQL; worker autoscaling depends on task 801 publishing queue age.
- Credentials: the application reads one `RXAUTH_DATABASE_URL`. Task 703 decides how rotated
  RDS credentials reach it: IAM database authentication, or a URL composed at task start from a
  Secrets Manager secret. A long-lived password in a task definition is not acceptable.
- Every value in the scaling and cost tables is provisional until measured (tasks 807, 903, 710).

## When to revisit

- If measured case duration needs more than about 20 workers, or burst cost approaches the budget.
- If measured availability misses 99.5% because of single-NAT or single-region design.
- When the commercial pilot (epic 1100) needs a different account, region or residency boundary.

## Approval

| Role | Name | Decision | Date |
|---|---|---|---|
| Release owner | Bavely Tawfik | Platform choices approved: ECS Fargate, RDS Multi-AZ prod / Single-AZ staging, Terraform, separate accounts in us-east-1 | 2026-10-22 |
| Release owner | Bavely Tawfik | Network, scaling, recovery and cost design; ADR accepted as a whole | 2026-10-22 |
