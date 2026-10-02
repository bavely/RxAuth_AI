"use server";

import { revalidatePath } from "next/cache";

import { ApiError, recordDecision } from "@/lib/api";
import { isReviewerAction, type DecisionState } from "@/lib/decisions";

/**
 * One form field as trimmed text.
 *
 * `FormData.get` returns `string | File`, and stringifying a `File` yields
 * "[object File]" — which would reach the API as a note, or as an action that
 * fails validation for a reason nobody could read. None of these fields is
 * ever a file, so anything that is one is discarded.
 *
 * Not exported: a `"use server"` module may only export async functions.
 */
function textField(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Record one reviewer decision (README §16).
 *
 * A server action, so the token and the API call stay on the server and the
 * form works without client-side JavaScript holding any API access of its own.
 *
 * The reviewer id is deliberately not a field here. The API takes it from the
 * verified token subject and ignores anything a client asserts, which is what
 * makes an append-only decision log worth keeping.
 *
 * This module exports exactly one async function. A `"use server"` file may
 * export nothing else — the state shape it returns lives in `lib/decisions.ts`.
 */
export async function submitDecision(
  runId: string,
  criterionId: string,
  _previous: DecisionState,
  formData: FormData,
): Promise<DecisionState> {
  const action = textField(formData, "action");
  if (!isReviewerAction(action)) {
    return { status: "error", message: `${action || "No action"} is not a reviewer action.` };
  }

  const correctedResult = textField(formData, "corrected_result");
  const note = textField(formData, "note");

  try {
    await recordDecision(runId, {
      criterionId,
      action,
      correctedResult: action === "corrected" ? correctedResult : null,
      note,
    });
  } catch (error) {
    if (error instanceof ApiError) {
      // 422 is the interesting one: `decision_from_evaluation` rejects a
      // "correction" that changes nothing, because a correction identical to
      // the original is an acceptance wearing the wrong label and would
      // pollute the gold records this log exports to.
      return { status: "error", message: error.detail };
    }
    return {
      status: "error",
      message: error instanceof Error ? error.message : "The decision was not recorded.",
    };
  }

  revalidatePath(`/runs/${runId}`);
  return { status: "recorded", message: `Recorded “${action}” for ${criterionId}.` };
}
