import { REVIEWER_ACTIONS, type ReviewerAction } from "@/lib/types";

/**
 * The shape a decision form carries between submits.
 *
 * Separate from the server action itself because a `"use server"` module may
 * only export async functions — a constant or a type alongside the action is a
 * build error. Keeping them here also means the client form imports no server
 * module to learn its own state shape.
 */
export interface DecisionState {
  status: "idle" | "recorded" | "error";
  message: string | null;
}

export const INITIAL_DECISION_STATE: DecisionState = { status: "idle", message: null };

export function isReviewerAction(value: string): value is ReviewerAction {
  return (REVIEWER_ACTIONS as readonly string[]).includes(value);
}

/** The same arrangement for the "run this case" action. */
export interface RunRequestState {
  status: "idle" | "queued" | "error";
  message: string | null;
}

export const INITIAL_RUN_REQUEST_STATE: RunRequestState = { status: "idle", message: null };
