# The reviewer read surface

The write path — create a case, upload documents, start a run, poll, read, decide — assumes a
caller that already holds an identifier. A script does. A person opening a browser does not.

This stage adds the three things a reviewer client needs and a script never did: somewhere to
start, the documents a case holds, and the bytes behind a citation. It adds no schema change and
no new dependency.

## Endpoints

| Method | Path | Role | Returns |
|---|---|---|---|
| `GET` | `/cases` | `case:read`, `case:write`, `case:review` | One page of the organization's cases, newest first, each with its document count and latest run |
| `GET` | `/cases/{case_id}/documents` | as above | Uploaded document metadata: filename, media type, size, SHA-256, retention date |
| `GET` | `/runs/{run_id}/documents/{document_id}/content` | as above | The original bytes of one cited document |

`GET /cases` takes `limit` (1–100, default 25) and `offset`, and returns `total` alongside the
page. A worklist that cannot say how many cases exist can only say "here are 25", and a reviewer
has no way to tell a full page from the end of the list.

A case that has never been run still appears, with `latest_run: null`. That case is usually the
one most in need of attention, so omitting it would invert the ordering the worklist exists for.

Every path is scoped to the organization on the verified token. A guessed identifier from another
tenant returns the same 404 as a resource that does not exist.

## The citation contract

`document_id` in the content path is the **run-local** id a `Provenance` carries — `D1`, `D2` —
not the upload's UUID. That is deliberate: the reviewer arrives from a citation, and a citation
names `D1`.

The guarantee is stronger than "a document comes back". A citation states a document, a page, and
a character range, and that range indexes into the bytes this endpoint serves:

```python
served = client.get(f"/runs/{run_id}/documents/D1/content").text
assert served[citation["start_char"] : citation["end_char"]] == citation["source_text"]
```

`tests/test_api.py::test_a_citation_can_be_opened_and_its_span_lands_where_it_claims` asserts
exactly that against a real run. It is the project's central promise, checked over HTTP rather
than asserted in prose.

There is deliberately **no** endpoint that re-projects citations out of a stored run. The run
payload already carries every span; re-deriving them server-side would be the second
implementation of the pipeline that the API exists to avoid.

### Joining a run's documents to the objects that were uploaded

`case_jobs` knows filenames. A run assigns `D1`, `D2` during ingestion. `save_case_run` stores an
object key against the run's ids, so the two have to be joined on filename — which is unique per
case, enforced by `uq_uploaded_document_filename`.

Before this stage they were not joined: the key dict was built by filename stem and read by
document id, so **every `storage_key` on the service path persisted as NULL**. The column looked
present and no citation could ever have been opened. The repository test that covered it passed a
correctly keyed dict straight to `save_case_run` and so never exercised the join.
`test_every_document_a_run_cited_can_be_fetched` now runs the real job and fetches every document
the run cited.

### Integrity and disposition

The stored object is verified against the SHA-256 recorded at upload before anything is returned.
A mismatch is a `502`, not a document — serving it would put a file of unknown provenance in
front of a reviewer as though it were evidence.

Documents are served `Content-Disposition: attachment` with `X-Content-Type-Options: nosniff` and
`Cache-Control: no-store, private`. Uploads accept several formats, and rendering one inline in
the reviewer's own origin would make the document viewer an XSS surface. A client that wants to
display a document fetches it and renders the blob itself.

A run started from the CLI reads documents from a directory and stores no object. Its citations
resolve, but the content endpoint returns a `404` that says so rather than a generic one.

## Browser origins

`RXAUTH_CORS_ALLOWED_ORIGINS` is a comma-separated origin list, empty by default. Empty means no
CORS headers are sent at all, which is correct for a service whose only clients are the CLI and
the worker. A policy nobody needs is an attack surface nobody is watching.

| Configuration | `local` | `staging` / `production` |
|---|---|---|
| Unset | No CORS headers | No CORS headers |
| `*` | Allowed | **Refused at startup** |
| `http://…` | Allowed (`http://localhost:3000`) | **Refused at startup** |
| `https://…` | Allowed | Allowed |
| Anything with a path, a trailing slash, or another scheme | **Refused at startup** | **Refused at startup** |

A wildcard on an authenticated PHI API lets any page on the internet spend a token it obtained;
plain HTTP lets a network attacker read patient documents out of the response. Neither is a
preference, so neither is a runtime warning — both fail startup.

The trailing-slash and path rules are not pedantry. A browser matches `scheme://host[:port]` and
nothing else, so accepting `https://reviewer.example.test/` would produce a policy that silently
blocks every request it was written to allow.

`allow_credentials` is off. This API authenticates with a bearer token the client attaches
itself, never with a cookie, so credentials mode would buy nothing and would forbid the wildcard
that local development is allowed to use. Allowed methods are `GET`, `POST`, and `OPTIONS`;
allowed headers are `Authorization` and `Content-Type`.

## What this stage does not do

- **No page-text endpoint.** Character offsets are positions within an *ingested page*, and for a
  scan that page text only exists after OCR. Re-ingesting inside a request handler would put
  minutes of CPU in a synchronous endpoint, which is the reason runs go to the job queue in the
  first place. For text documents the offsets index the file directly; for scans a client shows
  the page and the quoted span. Storing page text at run time is the honest fix and is a schema
  change with a measurement behind it, not a default.
- **No malware scanning**, unchanged from [production-hardening.md](production-hardening.md).
  Serving a stored document back to the organization that uploaded it does not widen that gap,
  but it does not close it either.
- **No reviewer UI.** This is the surface one would consume.
