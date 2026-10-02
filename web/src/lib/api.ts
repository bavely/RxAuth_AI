import "server-only";

import { resolveTokenProvider, type TokenProvider } from "@/lib/auth";
import type {
  CaseDocument,
  CaseRunListing,
  CaseSummary,
  ReviewerAction,
  ReviewerDecision,
  RunPayload,
  Worklist,
} from "@/lib/types";

/**
 * The server-side client for the RxAuth AI API.
 *
 * Every function here runs in the Next.js server. Nothing in this file is
 * reachable from the browser, which is what keeps the access token and the
 * document bytes out of client JavaScript.
 *
 * Responses are never cached. A reviewer worklist that shows a stale gate
 * result is worse than a slow one, and a cached response to a PHI request is a
 * copy of patient data in a place with no retention policy.
 */

const DEFAULT_TIMEOUT_SECONDS = 15;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly path: string,
  ) {
    super(`${status} from ${path}: ${detail}`);
    this.name = "ApiError";
  }

  /** True when the resource is absent *or* belongs to another organization. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** True when the API is up but has no database or queue configured. */
  get isUnavailable(): boolean {
    return this.status === 503;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

/** The API is not reachable at all — wrong URL, not running, DNS, timeout. */
export class ApiUnreachableError extends Error {
  constructor(
    readonly baseUrl: string,
    readonly cause: unknown,
  ) {
    super(
      `Could not reach the RxAuth AI API at ${baseUrl}. Set RXAUTH_API_URL, or start it with ` +
        `\`docker compose up\`.`,
    );
    this.name = "ApiUnreachableError";
  }
}

function baseUrl(env: NodeJS.ProcessEnv = process.env): string {
  return (env.RXAUTH_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

function timeoutMs(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env.RXAUTH_API_TIMEOUT_SECONDS ?? DEFAULT_TIMEOUT_SECONDS);
  const seconds = Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_TIMEOUT_SECONDS;
  return seconds * 1000;
}

async function authorization(provider: TokenProvider): Promise<Record<string, string>> {
  const token = await provider.token();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Pull a `detail` out of an error body without assuming there is one.
 *
 * FastAPI sends `{"detail": ...}`, but a proxy or a crash sends whatever it
 * likes, and a reviewer staring at `[object Object]` learns nothing.
 */
async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      return typeof detail === "string" ? detail : JSON.stringify(detail);
    }
    return JSON.stringify(body);
  } catch {
    return response.statusText || "The API returned an unreadable error.";
  }
}

export interface RequestOptions {
  provider?: TokenProvider;
  env?: NodeJS.ProcessEnv;
  method?: "GET" | "POST";
  body?: unknown;
}

/** One request to the API, with the token, timeout, and error shape applied. */
export async function apiFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const env = options.env ?? process.env;
  const provider = options.provider ?? resolveTokenProvider(env);
  const url = `${baseUrl(env)}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers: {
        Accept: "application/json",
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(await authorization(provider)),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: AbortSignal.timeout(timeoutMs(env)),
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiUnreachableError(baseUrl(env), cause);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await errorDetail(response), path);
  }
  return response;
}

async function getJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await apiFetch(path, options);
  return (await response.json()) as T;
}

/**
 * Assert the fields this UI actually reads.
 *
 * `models.py` is the single definition of every shape, and `types.ts` is a
 * hand-written mirror of it — so a rename on the Python side would otherwise
 * reach a component as `undefined` and render a blank cell rather than an
 * error. This turns that into a failure that names the endpoint.
 */
function requireFields<T>(value: unknown, fields: readonly string[], path: string): T {
  if (value === null || typeof value !== "object") {
    throw new ApiError(502, `Expected an object from ${path}.`, path);
  }
  const missing = fields.filter((field) => !(field in (value as Record<string, unknown>)));
  if (missing.length > 0) {
    throw new ApiError(
      502,
      `${path} is missing ${missing.join(", ")}. The API and this UI disagree about the shape.`,
      path,
    );
  }
  return value as T;
}

export async function fetchWorklist(
  { limit = 25, offset = 0 }: { limit?: number; offset?: number } = {},
  options: RequestOptions = {},
): Promise<Worklist> {
  const path = `/cases?limit=${limit}&offset=${offset}`;
  const body = await getJson<unknown>(path, options);
  const worklist = requireFields<Worklist>(body, ["total", "limit", "offset", "cases"], path);
  if (!Array.isArray(worklist.cases)) {
    throw new ApiError(502, `${path} returned a non-array \`cases\`.`, path);
  }
  return worklist;
}

export async function fetchCaseDocuments(
  caseId: string,
  options: RequestOptions = {},
): Promise<CaseDocument[]> {
  const path = `/cases/${encodeURIComponent(caseId)}/documents`;
  const body = await getJson<{ documents: CaseDocument[] }>(path, options);
  return requireFields<{ documents: CaseDocument[] }>(body, ["documents"], path).documents;
}

export async function fetchCaseRuns(
  caseId: string,
  options: RequestOptions = {},
): Promise<CaseRunListing[]> {
  const path = `/cases/${encodeURIComponent(caseId)}/runs`;
  const body = await getJson<{ runs: CaseRunListing[] }>(path, options);
  return requireFields<{ runs: CaseRunListing[] }>(body, ["runs"], path).runs;
}

export async function fetchRun(runId: string, options: RequestOptions = {}): Promise<RunPayload> {
  const path = `/runs/${encodeURIComponent(runId)}`;
  const body = await getJson<unknown>(path, options);
  const run = requireFields<RunPayload>(body, ["readiness", "policy", "assembly", "workflow"], path);
  requireFields(run.readiness, ["case_id", "criteria_total", "evaluations", "groundedness_gate"], path);
  return run;
}

export async function fetchDecisions(
  caseId: string,
  options: RequestOptions = {},
): Promise<ReviewerDecision[]> {
  const path = `/cases/${encodeURIComponent(caseId)}/decisions`;
  const body = await getJson<{ decisions: ReviewerDecision[] }>(path, options);
  return requireFields<{ decisions: ReviewerDecision[] }>(body, ["decisions"], path).decisions;
}

/**
 * One case's worklist row.
 *
 * The API has no `GET /cases/{id}`, so this reads the worklist and picks. That
 * is honest for a page of cases and would be the wrong shape at scale; the fix
 * is a case endpoint on the API, not a loop here.
 */
export async function fetchCaseSummary(
  caseId: string,
  options: RequestOptions = {},
): Promise<CaseSummary | null> {
  const worklist = await fetchWorklist({ limit: 100 }, options);
  return worklist.cases.find((entry) => entry.case_id === caseId) ?? null;
}

export interface DecisionInput {
  criterionId: string;
  action: ReviewerAction;
  correctedResult?: string | null;
  note?: string | null;
}

/** Record what a reviewer decided about one criterion (README §16). */
export async function recordDecision(
  runId: string,
  decision: DecisionInput,
  options: RequestOptions = {},
): Promise<{ decision_id: number; run_id: string; action: string }> {
  const path = `/runs/${encodeURIComponent(runId)}/decisions`;
  const response = await apiFetch(path, {
    ...options,
    method: "POST",
    body: {
      criterion_id: decision.criterionId,
      action: decision.action,
      // `null` and an absent key mean the same thing to the API, and sending
      // an empty note would store one that says nothing.
      corrected_result: decision.correctedResult || null,
      note: decision.note?.trim() || null,
    },
  });
  return (await response.json()) as { decision_id: number; run_id: string; action: string };
}

/** Start a run. Returns the job to poll; the work happens in the worker. */
export async function startRun(
  caseId: string,
  options: RequestOptions = {},
): Promise<{ job_id: string; status: string }> {
  const path = `/cases/${encodeURIComponent(caseId)}/runs`;
  const response = await apiFetch(path, { ...options, method: "POST" });
  return (await response.json()) as { job_id: string; status: string };
}

/**
 * The bytes of one cited document.
 *
 * Returns the `Response` rather than a parsed body so a caller can stream it or
 * read it as text without this function deciding which.
 */
export async function fetchCitedDocument(
  runId: string,
  documentId: string,
  options: RequestOptions = {},
): Promise<Response> {
  const path = `/runs/${encodeURIComponent(runId)}/documents/${encodeURIComponent(documentId)}/content`;
  return apiFetch(path, options);
}
