/**
 * The shapes the API returns.
 *
 * `models.py` is the single definition of every shape in this system, and these
 * are a hand-written mirror of the subset this UI reads. That duplication is
 * deliberate but not free: it is the one place a Python change can silently
 * break the UI, so `lib/api.ts` validates the fields it depends on at the
 * boundary rather than trusting the cast.
 */

export const CRITERION_RESULTS = [
  "SATISFIED",
  "NOT_SATISFIED",
  "MISSING",
  "AMBIGUOUS",
  "HUMAN_REVIEW_REQUIRED",
] as const;

export type CriterionResult = (typeof CRITERION_RESULTS)[number];

export const REVIEWER_ACTIONS = ["accepted", "corrected", "rejected", "escalated"] as const;

export type ReviewerAction = (typeof REVIEWER_ACTIONS)[number];

/** Where a value came from: document, page, and character range. */
export interface Provenance {
  document_id: string | null;
  filename: string | null;
  page: number | null;
  start_char: number | null;
  end_char: number | null;
  source_text: string | null;
}

export interface CriterionEvaluation {
  criterion_id: string;
  case_id: string;
  result: CriterionResult;
  supporting_evidence_ids: string[];
  candidate_evidence_ids: string[];
  confidence: number;
  evaluation_method: string;
  matcher_version: string;
  normalization_version: string | null;
  decision_trace: string[];
  explanation: string;
  criterion_description: string;
  policy_source: Provenance | null;
  patient_evidence_source: Provenance | null;
  patient_evidence_sources: Provenance[];
}

export interface ReadinessReport {
  case_id: string;
  policy_id: string;
  policy_version: string;
  policy_effective_date: string | null;
  matcher_version: string;
  payer: string;
  medication: string;
  indication: string;
  pa_required: boolean;
  documents_detected: number;
  mean_classification_confidence: number;
  documents_requiring_classification_review: number;
  evidence_total: number;
  evidence_requiring_review: number;
  criteria_total: number;
  criteria_satisfied: number;
  criteria_not_satisfied: number;
  criteria_missing: number;
  criteria_needs_review: number;
  criteria_unstructured: number;
  policy_exclusions_not_evaluated: number;
  groundedness_gate: "PASS" | "FAIL";
  evaluations: CriterionEvaluation[];
}

export interface AssembledDocument {
  id: string;
  filename: string;
  document_type: string;
  classification_confidence: number;
  page_count: number;
}

export interface WorkflowNodeRecord {
  name: string;
  status: string;
  attempts: number;
  summary: string | null;
  versions: Record<string, string>;
  error_type: string | null;
  error: string | null;
}

export interface ChecklistClaim {
  criterion_id: string;
  claim_type: string;
  text: string;
  evidence_ids: string[];
  policy_source: Provenance | null;
  patient_evidence_sources: Provenance[];
}

export interface DraftAssessment {
  criterion_id: string;
  claim_type: string;
  status: string;
  reason: string;
}

/** The whole stored run: exactly the document written to `reports/`. */
export interface RunPayload {
  readiness: ReadinessReport;
  policy: {
    selected: string | null;
    request_date: string | null;
    request_date_source: string | null;
    retrieval?: Record<string, unknown>;
  };
  assembly: {
    documents: AssembledDocument[];
    documents_requiring_classification_review: string[];
    evidence_total?: number;
  };
  workflow: { version: string; nodes: WorkflowNodeRecord[] };
  checklist: {
    generator_version: string;
    claims: ChecklistClaim[];
  } | null;
  draft_groundedness: { passed: boolean; assessments: DraftAssessment[] } | null;
}

export interface RunSummary {
  run_id: string;
  created_at: string;
  matcher_version: string;
  groundedness_gate: string;
  draft_gate: string | null;
  criteria: {
    total: number;
    satisfied: number;
    not_satisfied: number;
    missing: number;
    needs_review: number;
  };
}

export interface CaseSummary {
  case_id: string;
  created_at: string;
  patient_synthetic_id: string | null;
  payer: string | null;
  medication: string | null;
  indication: string | null;
  pa_required: boolean | null;
  documents: number;
  latest_run: RunSummary | null;
}

export interface Worklist {
  total: number;
  limit: number;
  offset: number;
  cases: CaseSummary[];
}

export interface CaseDocument {
  document_id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
  retain_until: string;
}

export interface ReviewerDecision {
  case_id: string;
  criterion_id: string;
  reviewer_id: string;
  action: ReviewerAction;
  recorded_at: string;
  original_result: CriterionResult;
  corrected_result: CriterionResult | null;
  note: string | null;
}

/** A run listed against its case, from `GET /cases/{id}/runs`. */
export interface CaseRunListing {
  run_id: string;
  created_at: string;
  summary: string;
  matcher_version: string;
  groundedness_gate: string;
}
