"use client";

import { useActionState } from "react";

import { INITIAL_RUN_REQUEST_STATE, type RunRequestState } from "@/lib/decisions";

/**
 * Queue a run for this case.
 *
 * Disabled with a reason when there are no documents, rather than hidden: a
 * reviewer looking at a case that cannot be run should be told why, not left
 * hunting for a control that is not there.
 */
export function StartRunButton({
  action,
  documentCount,
}: {
  action: (state: RunRequestState) => Promise<RunRequestState>;
  documentCount: number;
}) {
  const [state, formAction, pending] = useActionState(action, INITIAL_RUN_REQUEST_STATE);
  const runnable = documentCount > 0;

  return (
    <form action={formAction} className="stack">
      <button type="submit" className="secondary" disabled={pending || !runnable}>
        {pending ? "Queueing…" : "Run this case"}
      </button>
      {!runnable && <span className="small muted">Upload a document first.</span>}
      {state.status === "queued" && <output className="small muted">{state.message}</output>}
      {state.status === "error" && (
        <span className="small" style={{ color: "var(--bad)" }} role="alert">
          {state.message}
        </span>
      )}
    </form>
  );
}
