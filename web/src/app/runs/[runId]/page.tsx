import Link from "next/link";
import { notFound } from "next/navigation";

import { submitDecision } from "@/app/runs/[runId]/actions";
import { Citation, PolicyCitation } from "@/components/Citation";
import { DecisionForm } from "@/components/DecisionForm";
import { ErrorPanel } from "@/components/ErrorPanel";
import { ApiError, fetchRun } from "@/lib/api";
import { loadCitedDocuments, type CitedDocument } from "@/lib/documents";
import { formatConfidence, gateTone, resultLabel, resultTone } from "@/lib/format";
import type { CriterionEvaluation, RunPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * One run, in full.
 *
 * This is the page the whole system exists to produce: every requirement, the
 * evidence found for it, and the span behind every claim — with the document
 * fetched so the span can be read where it actually sits.
 */
export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;

  let run: RunPayload;
  try {
    run = await fetchRun(runId);
  } catch (error) {
    // `notFound()` throws Next's not-found signal, which propagates out of this
    // catch to render `not-found.tsx` with a real 404 status. Rendering the
    // explanation inside a 200 would make every monitor read a missing run as a
    // healthy page.
    if (error instanceof ApiError && error.isNotFound) {
      notFound();
    }
    return (
      <>
        <div className="page-head">
          <h1>Run</h1>
        </div>
        <ErrorPanel error={error} />
      </>
    );
  }

  const { readiness } = run;
  // Documents are fetched once for the page and shared by every citation that
  // points at them, rather than re-fetched per criterion.
  const documents = await loadCitedDocuments(runId, run.assembly.documents ?? []);

  return (
    <>
      <p className="crumb">
        <Link href="/">Worklist</Link> /{" "}
        <Link href={`/cases/${encodeURIComponent(readiness.case_id)}`} className="mono">
          {readiness.case_id}
        </Link>{" "}
        / <span className="mono">{runId.slice(0, 12)}…</span>
      </p>

      <div className="page-head">
        <h1>
          {readiness.medication} <span className="faint">for</span> {readiness.indication}
        </h1>
        <p>
          {readiness.payer} · policy <span className="mono">{readiness.policy_id}</span> version{" "}
          <span className="mono">{readiness.policy_version}</span>
          {readiness.policy_effective_date && ` (effective ${readiness.policy_effective_date})`}
        </p>
      </div>

      <ReadinessPanel readiness={readiness} />
      <PolicyPanel run={run} />

      <section>
        <div className="panel-head">
          <h2>Requirements</h2>
          <span className="small muted">{readiness.evaluations.length} evaluated</span>
        </div>
        {readiness.evaluations.map((evaluation) => (
          <CriterionCard
            key={evaluation.criterion_id}
            runId={runId}
            evaluation={evaluation}
            documents={documents}
          />
        ))}
      </section>

      <ChecklistPanel run={run} />
      <WorkflowPanel run={run} />
    </>
  );
}

function ReadinessPanel({ readiness }: { readiness: RunPayload["readiness"] }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Readiness</h2>
        <span className={`badge ${gateTone(readiness.groundedness_gate)}`}>
          citation gate {readiness.groundedness_gate}
        </span>
      </div>
      <div className="tiles">
        <Tile value={readiness.criteria_total} label="Requirements" />
        <Tile value={readiness.criteria_satisfied} label="Satisfied" tone="tile-ok" />
        <Tile value={readiness.criteria_not_satisfied} label="Not satisfied" tone="tile-bad" />
        <Tile value={readiness.criteria_missing} label="Missing" tone="tile-warn" />
        <Tile value={readiness.criteria_needs_review} label="Needs review" tone="tile-review" />
        <Tile value={readiness.evidence_total} label="Evidence facts" />
      </div>
      <p className="panel-note" style={{ marginTop: "0.9rem", marginBottom: 0 }}>
        {readiness.policy_exclusions_not_evaluated > 0 && (
          <>
            {readiness.policy_exclusions_not_evaluated} policy exclusion
            {readiness.policy_exclusions_not_evaluated === 1 ? " was" : "s were"} not evaluated and
            still need a human.{" "}
          </>
        )}
        {readiness.criteria_unstructured > 0 && (
          <>
            {readiness.criteria_unstructured} requirement
            {readiness.criteria_unstructured === 1 ? "" : "s"} could not be turned into a
            machine-checkable rule and {readiness.criteria_unstructured === 1 ? "is" : "are"} kept
            as prose.{" "}
          </>
        )}
        {readiness.evidence_requiring_review > 0 && (
          <>
            {readiness.evidence_requiring_review} extracted fact
            {readiness.evidence_requiring_review === 1 ? "" : "s"} fell below the confidence
            threshold and {readiness.evidence_requiring_review === 1 ? "was" : "were"} routed for
            review.
          </>
        )}
      </p>
    </section>
  );
}

function Tile({ value, label, tone }: { value: number; label: string; tone?: string }) {
  return (
    <div className={`tile ${tone ?? ""}`}>
      <div className="tile-value">{value}</div>
      <div className="tile-label">{label}</div>
    </div>
  );
}

function PolicyPanel({ run }: { run: RunPayload }) {
  return (
    <section className="panel">
      <h2 className="subhead">Which policy, and why that version</h2>
      <dl className="facts">
        <div>
          <dt>Selected</dt>
          <dd className="mono">{run.policy.selected ?? "—"}</dd>
        </div>
        <div>
          <dt>Request date</dt>
          <dd className="mono">{run.policy.request_date ?? "—"}</dd>
        </div>
        <div>
          <dt>Date source</dt>
          <dd className="small muted">{run.policy.request_date_source ?? "—"}</dd>
        </div>
        <div>
          <dt>Matcher</dt>
          <dd className="mono small">{run.readiness.matcher_version}</dd>
        </div>
      </dl>
    </section>
  );
}

function CriterionCard({
  runId,
  evaluation,
  documents,
}: {
  runId: string;
  evaluation: CriterionEvaluation;
  documents: Map<string, CitedDocument>;
}) {
  const sources =
    evaluation.patient_evidence_sources.length > 0
      ? evaluation.patient_evidence_sources
      : evaluation.patient_evidence_source
        ? [evaluation.patient_evidence_source]
        : [];

  return (
    <article className="criterion">
      <header className="criterion-head">
        <span className="criterion-id">{evaluation.criterion_id}</span>
        <div className="criterion-text">
          {evaluation.criterion_description}
          <p className="criterion-explanation">{evaluation.explanation}</p>
        </div>
        <div className="stack">
          <span className={`badge ${resultTone(evaluation.result)}`}>
            {resultLabel(evaluation.result)}
          </span>
          <span className="badge badge-neutral">{formatConfidence(evaluation.confidence)}</span>
        </div>
      </header>

      <div className="criterion-body">
        {evaluation.policy_source && (
          <>
            <h3 className="subhead">What the policy requires</h3>
            <PolicyCitation provenance={evaluation.policy_source} />
          </>
        )}

        <h3 className="subhead">
          {sources.length > 0 ? "Evidence in the record" : "No evidence cited"}
        </h3>
        {sources.length === 0 ? (
          <p className="small muted">
            Nothing in the packet was matched to this requirement. That is a finding, not a
            failure — it is what the reviewer needs to chase.
          </p>
        ) : (
          sources.map((provenance, index) => (
            <Citation
              key={`${provenance.document_id}-${provenance.start_char}-${index}`}
              provenance={provenance}
              document={
                provenance.document_id ? documents.get(provenance.document_id) : undefined
              }
            />
          ))
        )}

        {evaluation.decision_trace.length > 0 && (
          <details className="trace">
            <summary>
              How this was decided ({evaluation.evaluation_method}, {evaluation.matcher_version})
            </summary>
            <ol>
              {evaluation.decision_trace.map((step, index) => (
                <li key={index}>{step}</li>
              ))}
            </ol>
          </details>
        )}

        <DecisionForm
          criterionId={evaluation.criterion_id}
          currentResult={evaluation.result}
          action={submitDecision.bind(null, runId, evaluation.criterion_id)}
        />
      </div>
    </article>
  );
}

function ChecklistPanel({ run }: { run: RunPayload }) {
  if (!run.checklist || run.checklist.claims.length === 0) return null;

  const assessments = new Map(
    (run.draft_groundedness?.assessments ?? []).map((item) => [item.criterion_id, item]),
  );

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Drafted checklist</h2>
        <span className={`badge ${gateTone(run.draft_groundedness?.passed ? "PASS" : "FAIL")}`}>
          draft gate {run.draft_groundedness?.passed ? "PASS" : "FAIL"}
        </span>
      </div>
      <p className="panel-note">
        One sentence per requirement, quoting the record verbatim. Every value and medication in a
        sentence must appear in a span that sentence cites, or the gate rejects it.
      </p>
      <div className="table-scroll">
        <table className="grid">
          <thead>
            <tr>
              <th>Criterion</th>
              <th>Drafted sentence</th>
              <th>Grounded</th>
            </tr>
          </thead>
          <tbody>
            {run.checklist.claims.map((claim) => {
              const assessment = assessments.get(claim.criterion_id);
              const grounded = assessment?.status === "grounded";
              return (
                <tr key={`${claim.criterion_id}-${claim.claim_type}`}>
                  <td className="mono small">{claim.criterion_id}</td>
                  <td className="small">{claim.text}</td>
                  <td>
                    <span className={`badge ${grounded ? "badge-ok" : "badge-bad"}`}>
                      {assessment?.status ?? "unassessed"}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function WorkflowPanel({ run }: { run: RunPayload }) {
  const nodes = run.workflow?.nodes ?? [];
  if (nodes.length === 0) return null;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Workflow</h2>
        <span className="mono small muted">{run.workflow.version}</span>
      </div>
      <p className="panel-note">
        Each node records how it ended. A failure states that every node after it never ran, rather
        than leaving a partial result looking complete.
      </p>
      <div className="table-scroll">
        <table className="grid">
          <thead>
            <tr>
              <th>Node</th>
              <th>Status</th>
              <th>Summary</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((node) => (
              <tr key={node.name}>
                <td className="mono small">{node.name}</td>
                <td>
                  <span className={`badge ${node.status === "ok" ? "badge-ok" : "badge-bad"}`}>
                    {node.status}
                  </span>
                </td>
                <td className="small muted">{node.summary ?? node.error ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export async function generateMetadata({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  // The title carries no patient or case identifier: it becomes a browser tab,
  // a bookmark, and a history entry, none of which have a retention policy.
  return { title: `Run ${runId.slice(0, 8)} — RxAuth AI` };
}

