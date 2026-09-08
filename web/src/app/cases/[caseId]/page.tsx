import Link from "next/link";
import { notFound } from "next/navigation";

import { startCaseRun } from "@/app/cases/[caseId]/actions";
import { ErrorPanel } from "@/components/ErrorPanel";
import { StartRunButton } from "@/components/StartRunButton";
import {
  ApiError,
  fetchCaseDocuments,
  fetchCaseRuns,
  fetchCaseSummary,
  fetchDecisions,
} from "@/lib/api";
import { formatBytes, formatTimestamp, gateTone } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * One case: what was declared, what was uploaded, what has been run, and what a
 * reviewer has already decided.
 *
 * The four reads are independent, so they run together — a page that fetches
 * sequentially is four round trips deep before it renders anything.
 */
export default async function CasePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await params;

  let summary, documents, runs, decisions;
  try {
    [summary, documents, runs, decisions] = await Promise.all([
      fetchCaseSummary(caseId),
      fetchCaseDocuments(caseId),
      fetchCaseRuns(caseId),
      fetchDecisions(caseId),
    ]);
  } catch (error) {
    // `notFound()` throws Next's not-found signal, which propagates out of this
    // catch to render `not-found.tsx` with a real 404 status.
    if (error instanceof ApiError && error.isNotFound) {
      notFound();
    }
    return (
      <>
        <div className="page-head">
          <h1 className="mono">{caseId}</h1>
        </div>
        <ErrorPanel error={error} />
      </>
    );
  }

  return (
    <>
      <p className="crumb">
        <Link href="/">Worklist</Link> / <span className="mono">{caseId}</span>
      </p>
      <div className="page-head">
        <h1 className="mono">{caseId}</h1>
        <p>
          {summary
            ? `${summary.medication ?? "—"} for ${summary.indication ?? "—"}`
            : "This case has no worklist entry on the first page of results."}
        </p>
      </div>

      {summary && (
        <section className="panel">
          <h2 className="subhead">Declared facts</h2>
          <p className="panel-note">
            These come from the case manifest, not from any document. <code>pa_required</code> in
            particular is declared input — a public policy cannot establish a member&rsquo;s benefit
            status, so the system never infers it.
          </p>
          <dl className="facts">
            <div>
              <dt>Patient</dt>
              <dd className="mono">{summary.patient_synthetic_id ?? "—"}</dd>
            </div>
            <div>
              <dt>Payer</dt>
              <dd>{summary.payer ?? "—"}</dd>
            </div>
            <div>
              <dt>Medication</dt>
              <dd>{summary.medication ?? "—"}</dd>
            </div>
            <div>
              <dt>Indication</dt>
              <dd>{summary.indication ?? "—"}</dd>
            </div>
            <div>
              <dt>PA required</dt>
              <dd>{summary.pa_required === null ? "—" : summary.pa_required ? "Yes" : "No"}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd className="small">{formatTimestamp(summary.created_at)}</dd>
            </div>
          </dl>
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>Documents</h2>
          <span className="small muted">{documents.length} uploaded</span>
        </div>
        {documents.length === 0 ? (
          <p className="empty">No documents uploaded. A run needs at least one.</p>
        ) : (
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Type</th>
                  <th className="numeric">Size</th>
                  <th>SHA-256</th>
                  <th>Retained until</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => (
                  <tr key={document.document_id}>
                    <td className="mono">{document.filename}</td>
                    <td className="small muted">{document.media_type}</td>
                    <td className="numeric small">{formatBytes(document.size_bytes)}</td>
                    {/* Enough digest to compare against an upload receipt by eye,
                        without a column of 64 characters in every row. */}
                    <td className="mono small faint" title={document.sha256}>
                      {document.sha256.slice(0, 12)}…
                    </td>
                    <td className="small muted">{formatTimestamp(document.retain_until)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Runs</h2>
          <StartRunButton
            action={startCaseRun.bind(null, caseId)}
            documentCount={documents.length}
          />
        </div>
        <p className="panel-note">
          Running a case again keeps both runs. Overwriting would destroy the comparison that makes
          &ldquo;did the new matcher change anything?&rdquo; answerable.
        </p>
        {runs.length === 0 ? (
          <p className="empty">This case has not been run.</p>
        ) : (
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Summary</th>
                  <th>Matcher</th>
                  <th>Gate</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link href={`/runs/${encodeURIComponent(run.run_id)}`} className="mono small">
                        {run.run_id.slice(0, 12)}…
                      </Link>
                      <div className="small faint">{formatTimestamp(run.created_at)}</div>
                    </td>
                    <td className="small">{run.summary}</td>
                    <td className="mono small muted">{run.matcher_version}</td>
                    <td>
                      <span className={`badge ${gateTone(run.groundedness_gate)}`}>
                        {run.groundedness_gate}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Reviewer decisions</h2>
          <span className="small muted">{decisions.length} recorded</span>
        </div>
        <p className="panel-note">
          Append-only. A superseding decision is another row, not an edit — a correction that can be
          rewritten later is not a record of what a reviewer thought at the time.
        </p>
        {decisions.length === 0 ? (
          <p className="empty">Nothing recorded yet. Decisions are made on a run.</p>
        ) : (
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Criterion</th>
                  <th>Action</th>
                  <th>Original</th>
                  <th>Corrected</th>
                  <th>Reviewer</th>
                  <th>Recorded</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((decision, index) => (
                  <tr key={`${decision.criterion_id}-${decision.recorded_at}-${index}`}>
                    <td className="mono small">{decision.criterion_id}</td>
                    <td className="small">{decision.action}</td>
                    <td className="small muted">{decision.original_result}</td>
                    <td className="small">{decision.corrected_result ?? "—"}</td>
                    <td className="mono small muted">{decision.reviewer_id}</td>
                    <td className="small faint">{formatTimestamp(decision.recorded_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
