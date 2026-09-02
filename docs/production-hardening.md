# Upload and durability hardening

This stage keeps the deployment dependency set to AWS services and PostgreSQL. It does not add
Redis, SQS, RabbitMQ, or a malware-scanning service.

## Upload policy

The initial limits are deliberately conservative for an OCR-heavy workload and can be changed
after representative load testing:

| Boundary | Initial value | Setting |
|---|---:|---|
| One document | 25 MiB | `RXAUTH_UPLOAD_MAX_FILE_BYTES` |
| One case, all documents | 250 MiB | `RXAUTH_UPLOAD_MAX_CASE_BYTES` |
| Documents per case | 20 | `RXAUTH_UPLOAD_MAX_DOCUMENTS_PER_CASE` |
| PDF pages | 100 | `RXAUTH_UPLOAD_MAX_PDF_PAGES` |
| Decoded image pixels, including frames | 50 million | `RXAUTH_UPLOAD_MAX_IMAGE_PIXELS` |
| Multipart envelope above the file limit | 1 MiB | `RXAUTH_UPLOAD_MULTIPART_OVERHEAD_BYTES` |

The API bounds the request body before multipart parsing, then streams the file in bounded chunks
to a hidden staging path while calculating SHA-256. It does not read the complete document into
application memory. Empty files, unsafe filenames, duplicate names, malformed content, and files
whose signature disagrees with the extension are rejected. Text must be UTF-8 without NUL bytes;
PDFs must be readable and within the page limit; images must verify and remain within the decoded
pixel limit.

The existing format set remains unchanged: `.txt`, `.md`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif`,
`.tiff`, and `.bmp`.

Malware scanning is explicitly deferred. Before real patient documents are accepted, add an
asynchronous quarantine/scan/release state (for example, S3 event-driven scanning) and ensure the
worker can read only released objects. Extension, signature, parser, and size validation reduce
parser risk but are not malware detection.

## Durable state and work

Case manifests, uploaded-document metadata, and jobs are PostgreSQL rows. Document bytes remain in
S3. The API enqueues work transactionally and a separate `rxauth-worker` process claims due jobs
using `FOR UPDATE SKIP LOCKED`. A lease heartbeat prevents another healthy worker from taking a
long-running job; an expired lease makes abandoned work eligible for retry. On restart, a worker
rebuilds a missing local case directory from S3 and verifies every object against its stored SHA-256
before processing it.

The job ID is reused as the case-run ID. If a worker persists a run and exits before acknowledging
the job, the retry finds and returns the existing run instead of executing it twice.

`docker compose up --build` now runs migrations first, then starts the API and worker. A deployed
AWS service should run the same three process roles separately: migration task, API service, and
worker service, all against the same PostgreSQL database and S3 bucket.

## Retention

| Data | Retention | Enforcement |
|---|---:|---|
| Original documents | 10 calendar years | PostgreSQL `retain_until` plus S3 Object Lock retain-until on upload |
| Temporary processing copies | 72 hours | Hourly worker maintenance removes eligible tenant working directories |
| Successful jobs | 6 calendar years | PostgreSQL `expires_at`; hourly worker purge |
| Terminal failed jobs | 90 days | PostgreSQL `expires_at`; hourly worker purge |

`RXAUTH_S3_OBJECT_LOCK_MODE` defaults to `COMPLIANCE`, and production rejects a Governance
override. The bucket must have versioning and Object Lock enabled when it is created. The
application cannot add Object Lock to an existing bucket. In Compliance mode nobody, including the
root user, can shorten or bypass the ten-year retain-until date.

## Retry policy

The durable queue implements three total attempts with exponential backoff and full jitter. After
the first failure, the retry waits randomly from zero through 30 minutes. After the second failure,
the final attempt waits randomly from zero through 60 minutes. The configured ceilings are
`RXAUTH_JOB_RETRY_INITIAL_SECONDS=1800` and `RXAUTH_JOB_RETRY_MAX_SECONDS=3600`.

The crash-recovery lease is 15 minutes (`RXAUTH_JOB_LEASE_SECONDS=900`) and a healthy worker renews
it every five minutes (`RXAUTH_JOB_HEARTBEAT_SECONDS=300`). The heartbeat must remain shorter than
the lease. This lease detects a stopped worker; it is not an execution timeout. Per the current
operating decision, attempts have no hard maximum runtime.

Staging and production also fail startup unless `RXAUTH_DATABASE_URL` is present and uses a
PostgreSQL dialect. SQLite and local document storage remain development/test conveniences only.
