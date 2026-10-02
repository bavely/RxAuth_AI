import type { CriterionResult } from "@/lib/types";

/** Presentation helpers. No API access, no state — safe on either side. */

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  // A fixed locale and an explicit UTC zone: a reviewer comparing a run to a
  // colleague's screenshot should not have to work out whose timezone won.
  return `${parsed.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(0)}%`;
}

/** Badge class for one of the five criterion results. */
export function resultTone(result: CriterionResult | string): string {
  switch (result) {
    case "SATISFIED":
      return "badge-ok";
    case "NOT_SATISFIED":
      return "badge-bad";
    case "MISSING":
      return "badge-warn";
    case "AMBIGUOUS":
    case "HUMAN_REVIEW_REQUIRED":
      return "badge-review";
    default:
      return "badge-neutral";
  }
}

/**
 * `HUMAN_REVIEW_REQUIRED` is 22 characters and appears in every table row.
 *
 * The five states are the point of the system — three of them mean "I do not
 * know" — so they are never collapsed into a generic "needs attention", only
 * shortened.
 */
export function resultLabel(result: CriterionResult | string): string {
  switch (result) {
    case "NOT_SATISFIED":
      return "NOT SATISFIED";
    case "HUMAN_REVIEW_REQUIRED":
      return "HUMAN REVIEW";
    default:
      return String(result);
  }
}

export function gateTone(gate: string | null | undefined): string {
  if (gate === "PASS") return "badge-ok";
  if (gate === "FAIL") return "badge-bad";
  return "badge-neutral";
}
