"use server";

import { revalidatePath } from "next/cache";

import { ApiError, startRun } from "@/lib/api";
import type { RunRequestState } from "@/lib/decisions";

/**
 * Queue a run for one case.
 *
 * The API answers 202 with a job, not a finished run: a case is seconds on text
 * and minutes on a scanned packet, and holding the request open would make the
 * reviewer's browser the thing that times out. The worker does the work, so
 * this returns as soon as the job is durable and the page shows the new run on
 * the next load.
 *
 * Starting a run needs `case:write`. A reviewer holding only `case:read` gets a
 * 403, which is surfaced rather than hidden — the button is not the place to
 * re-implement the API's authorization.
 */
export async function startCaseRun(
  caseId: string,
  _previous: RunRequestState,
): Promise<RunRequestState> {
  try {
    const job = await startRun(caseId);
    revalidatePath(`/cases/${caseId}`);
    return {
      status: "queued",
      message: `Queued. Job ${job.job_id.slice(0, 8)}… is ${job.status}; the run appears here when the worker finishes.`,
    };
  } catch (error) {
    if (error instanceof ApiError) {
      return { status: "error", message: error.detail };
    }
    return {
      status: "error",
      message: error instanceof Error ? error.message : "The run was not queued.",
    };
  }
}
