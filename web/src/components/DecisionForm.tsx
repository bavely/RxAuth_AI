"use client";

import { useActionState, useState } from "react";

import { INITIAL_DECISION_STATE, type DecisionState } from "@/lib/decisions";
import { CRITERION_RESULTS, REVIEWER_ACTIONS } from "@/lib/types";

/**
 * Record what a reviewer decided about one criterion.
 *
 * The only client component in the application. It needs interactivity for one
 * reason: the corrected-result field is meaningless unless the action is
 * "corrected", and showing it always invites a reviewer to fill in a
 * correction that will then be ignored.
 *
 * It holds no token and calls no API. The submit goes to a server action, so
 * everything privileged stays on the server side of the boundary.
 */
export function DecisionForm({
  criterionId,
  currentResult,
  action,
}: {
  criterionId: string;
  currentResult: string;
  action: (state: DecisionState, formData: FormData) => Promise<DecisionState>;
}) {
  const [state, formAction, pending] = useActionState(action, INITIAL_DECISION_STATE);
  const [reviewerAction, setReviewerAction] = useState<string>("accepted");

  return (
    <form className="decision" action={formAction}>
      <h3 className="subhead">Reviewer decision</h3>
      <div className="decision-row">
        <div className="field">
          <label htmlFor={`action-${criterionId}`}>Action</label>
          <select
            id={`action-${criterionId}`}
            name="action"
            value={reviewerAction}
            onChange={(event) => setReviewerAction(event.target.value)}
            disabled={pending}
          >
            {REVIEWER_ACTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        {reviewerAction === "corrected" && (
          <div className="field">
            <label htmlFor={`corrected-${criterionId}`}>Corrected result</label>
            <select id={`corrected-${criterionId}`} name="corrected_result" disabled={pending}>
              {CRITERION_RESULTS.filter((result) => result !== currentResult).map((result) => (
                <option key={result} value={result}>
                  {result}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="field field-wide">
          <label htmlFor={`note-${criterionId}`}>Note (optional)</label>
          <input
            id={`note-${criterionId}`}
            name="note"
            type="text"
            placeholder="Why, in a sentence"
            disabled={pending}
          />
        </div>

        <button type="submit" disabled={pending}>
          {pending ? "Recording…" : "Record"}
        </button>
      </div>

      {state.status === "recorded" && (
        // `<output>` rather than role="status": it carries the live-region
        // semantics natively, so a screen reader announces the confirmation
        // without depending on how a given device maps the ARIA role.
        <output className="citation-warning" style={{ color: "var(--ok)" }}>
          {state.message}
        </output>
      )}
      {state.status === "error" && (
        <p className="citation-warning citation-warning-bad" role="alert">
          {state.message}
        </p>
      )}
      <p className="small faint" style={{ margin: "0.5rem 0 0" }}>
        Recorded against your verified identity and kept permanently. A later change is another
        record, never an edit.
      </p>
    </form>
  );
}
