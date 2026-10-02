import Link from "next/link";

import { ErrorPanel } from "@/components/ErrorPanel";
import { fetchWorklist } from "@/lib/api";
import { formatTimestamp, gateTone, resultTone } from "@/lib/format";
import type { CaseSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 25;

/**
 * The worklist.
 *
 * Ordering is the API's — newest first — and is not re-sorted here. A case that
 * has never run shows "Not run" rather than being filtered out or pushed to the
 * bottom: it is usually the one most in need of attention, and a worklist that
 * hides work is worse than no worklist.
 */
export default async function WorklistPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const { page } = await searchParams;
  const pageNumber = Math.max(1, Number.parseInt(page ?? "1", 10) || 1);
  const offset = (pageNumber - 1) * PAGE_SIZE;

  let worklist;
  try {
    worklist = await fetchWorklist({ limit: PAGE_SIZE, offset });
  } catch (error) {
    return (
      <>
        <div className="page-head">
          <h1>Worklist</h1>
        </div>
        <ErrorPanel error={error} />
      </>
    );
  }

  const lastPage = Math.max(1, Math.ceil(worklist.total / PAGE_SIZE));

  return (
    <>
      <div className="page-head">
        <h1>Worklist</h1>
        <p>
          {worklist.total === 0
            ? "No cases yet."
            : `${worklist.total} case${worklist.total === 1 ? "" : "s"} in this organization, newest first.`}
        </p>
      </div>

      {worklist.cases.length === 0 ? (
        <div className="panel">
          <p className="empty">
            Nothing to review. Create a case and upload its documents through the API, then start a
            run.
          </p>
        </div>
      ) : (
        <div className="panel">
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Medication / indication</th>
                  <th>Payer</th>
                  <th className="numeric">Docs</th>
                  <th>Last run</th>
                  <th>Readiness</th>
                </tr>
              </thead>
              <tbody>
                {worklist.cases.map((entry) => (
                  <WorklistRow key={entry.case_id} entry={entry} />
                ))}
              </tbody>
            </table>
          </div>

          {lastPage > 1 && (
            <div className="pager">
              <span>
                Page {pageNumber} of {lastPage}
              </span>
              <span className="stack">
                {pageNumber > 1 && <Link href={`/?page=${pageNumber - 1}`}>← Previous</Link>}
                {pageNumber < lastPage && <Link href={`/?page=${pageNumber + 1}`}>Next →</Link>}
              </span>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function WorklistRow({ entry }: { entry: CaseSummary }) {
  const run = entry.latest_run;
  return (
    <tr>
      <td>
        <Link href={`/cases/${encodeURIComponent(entry.case_id)}`} className="mono">
          {entry.case_id}
        </Link>
        <div className="small faint">{entry.patient_synthetic_id ?? "—"}</div>
      </td>
      <td>
        {entry.medication ?? "—"}
        <div className="small muted">{entry.indication ?? "—"}</div>
      </td>
      <td className="small">{entry.payer ?? "—"}</td>
      <td className="numeric">{entry.documents}</td>
      <td className="small">
        {run ? (
          <>
            <Link href={`/runs/${encodeURIComponent(run.run_id)}`}>
              {formatTimestamp(run.created_at)}
            </Link>
            <div className="faint mono">{run.matcher_version}</div>
          </>
        ) : (
          <span className="muted">Not run</span>
        )}
      </td>
      <td>
        {run ? (
          <ReadinessCell run={run} />
        ) : (
          <span className="muted small">
            {entry.documents === 0 ? "No documents uploaded" : "Ready to run"}
          </span>
        )}
      </td>
    </tr>
  );
}

/**
 * Counts, not a score.
 *
 * There is deliberately no single "readiness percentage": the five states are
 * not points on one axis, and averaging "missing" against "satisfied" would
 * invent a number the pipeline never computed.
 */
function ReadinessCell({ run }: { run: NonNullable<CaseSummary["latest_run"]> }) {
  const { criteria } = run;
  return (
    <div className="stack">
      <span className={`badge ${resultTone("SATISFIED")}`}>{criteria.satisfied} met</span>
      {criteria.not_satisfied > 0 && (
        <span className={`badge ${resultTone("NOT_SATISFIED")}`}>
          {criteria.not_satisfied} not met
        </span>
      )}
      {criteria.missing > 0 && (
        <span className={`badge ${resultTone("MISSING")}`}>{criteria.missing} missing</span>
      )}
      {criteria.needs_review > 0 && (
        <span className={`badge ${resultTone("HUMAN_REVIEW_REQUIRED")}`}>
          {criteria.needs_review} review
        </span>
      )}
      <span className={`badge ${gateTone(run.groundedness_gate)}`}>
        gate {run.groundedness_gate}
      </span>
    </div>
  );
}
