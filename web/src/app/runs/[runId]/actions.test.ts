import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * `@/lib/api` is mocked wholesale rather than partially.
 *
 * It imports `server-only`, which throws outside a React Server Component — so
 * the real module cannot be loaded in a test process at all. The fake
 * `ApiError` below is the class the action does `instanceof` against, because
 * the action imports it from this mock.
 */
class FakeApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly path: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

const recordDecision = vi.fn();

vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/api", () => ({
  ApiError: FakeApiError,
  recordDecision: (...args: unknown[]) => recordDecision(...args),
}));

const { submitDecision } = await import("@/app/runs/[runId]/actions");
const { INITIAL_DECISION_STATE } = await import("@/lib/decisions");

function form(fields: Record<string, string | File>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    data.set(key, value);
  }
  return data;
}

beforeEach(() => {
  recordDecision.mockReset();
  recordDecision.mockResolvedValue({ decision_id: 1, run_id: "run-1", action: "accepted" });
});

describe("submitDecision", () => {
  it("records an acceptance against the criterion it was rendered for", async () => {
    const state = await submitDecision(
      "run-1",
      "C1",
      INITIAL_DECISION_STATE,
      form({ action: "accepted", note: "Checked the cited span." }),
    );

    expect(state.status).toBe("recorded");
    expect(recordDecision).toHaveBeenCalledWith("run-1", {
      criterionId: "C1",
      action: "accepted",
      correctedResult: null,
      note: "Checked the cited span.",
    });
  });

  it("passes a corrected result only when the action is a correction", async () => {
    await submitDecision(
      "run-1",
      "C2",
      INITIAL_DECISION_STATE,
      form({ action: "corrected", corrected_result: "MISSING" }),
    );

    expect(recordDecision.mock.calls[0]?.[1]).toMatchObject({
      action: "corrected",
      correctedResult: "MISSING",
    });
  });

  it("drops a stale corrected result when the action is not a correction", async () => {
    // The field stays in the DOM long enough for a reviewer to pick a value and
    // then change the action. Sending it anyway would record a correction
    // nobody asked for.
    await submitDecision(
      "run-1",
      "C3",
      INITIAL_DECISION_STATE,
      form({ action: "escalated", corrected_result: "SATISFIED" }),
    );

    expect(recordDecision.mock.calls[0]?.[1]).toMatchObject({
      action: "escalated",
      correctedResult: null,
    });
  });

  it("refuses an action the API does not define, without calling it", async () => {
    const state = await submitDecision(
      "run-1",
      "C1",
      INITIAL_DECISION_STATE,
      form({ action: "approved" }),
    );

    expect(state.status).toBe("error");
    expect(state.message).toContain("not a reviewer action");
    expect(recordDecision).not.toHaveBeenCalled();
  });

  it("refuses an empty action rather than sending one", async () => {
    const state = await submitDecision("run-1", "C1", INITIAL_DECISION_STATE, form({}));

    expect(state.status).toBe("error");
    expect(recordDecision).not.toHaveBeenCalled();
  });

  it("discards a file where text was expected", async () => {
    // `FormData.get` returns `string | File`, and stringifying a File yields
    // "[object File]" — which would otherwise be stored as a reviewer's note.
    await submitDecision(
      "run-1",
      "C1",
      INITIAL_DECISION_STATE,
      form({ action: "accepted", note: new File(["x"], "note.txt") }),
    );

    expect(recordDecision.mock.calls[0]?.[1]).toMatchObject({ note: "" });
  });

  it("surfaces the API's reason for rejecting a correction", async () => {
    // 422 is the one that matters: a "correction" identical to the original is
    // an acceptance wearing the wrong label, and the API refuses it.
    recordDecision.mockRejectedValue(
      new FakeApiError(422, "A correction must change the result.", "/runs/run-1/decisions"),
    );

    const state = await submitDecision(
      "run-1",
      "C1",
      INITIAL_DECISION_STATE,
      form({ action: "corrected", corrected_result: "SATISFIED" }),
    );

    expect(state.status).toBe("error");
    expect(state.message).toBe("A correction must change the result.");
  });

  it("reports an unreachable API instead of claiming the decision was recorded", async () => {
    recordDecision.mockRejectedValue(new Error("Could not reach the RxAuth AI API."));

    const state = await submitDecision(
      "run-1",
      "C1",
      INITIAL_DECISION_STATE,
      form({ action: "accepted" }),
    );

    expect(state.status).toBe("error");
    expect(state.message).toContain("Could not reach");
  });

  it("trims surrounding whitespace from a note", async () => {
    await submitDecision(
      "run-1",
      "C1",
      INITIAL_DECISION_STATE,
      form({ action: "accepted", note: "   spaced out   " }),
    );

    expect(recordDecision.mock.calls[0]?.[1]).toMatchObject({ note: "spaced out" });
  });
});
