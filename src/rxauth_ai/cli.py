"""RxAuth AI — Milestone 0 runner.

Runs one synthetic PA case end to end (Python only, no DB, no LLM, no network)
and prints an intake summary plus a per-criterion evidence trace, then writes the
structured result to reports/case_PA-DEMO-001.json.

Usage:
    rxauth-milestone0
    rxauth-milestone0 --json-only
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import get_settings
from .models import CriterionResult
from .pipeline import run_pipeline
from .synthetic_case import build_case, build_policy

_ICON = {
    CriterionResult.SATISFIED: "[ok ]",
    CriterionResult.NOT_SATISFIED: "[no ]",
    CriterionResult.MISSING: "[gap]",
    CriterionResult.AMBIGUOUS: "[?? ]",
    CriterionResult.HUMAN_REVIEW_REQUIRED: "[hum]",
}


def print_report(report, evaluations) -> None:
    line = "-" * 56
    print("\nPrior Authorization Intake Summary")
    print(line)
    print(f"Case:                    {report.case_id}")
    print(f"Payer / policy:          {report.payer} / {report.policy_id} v{report.policy_version}")
    print(f"Policy effective:        {report.policy_effective_date or 'not recorded'}")
    print(f"Medication / indication: {report.medication} / {report.indication}")
    print(f"PA required (trigger):   {report.pa_required}")
    print(f"Documents detected:      {report.documents_detected}")
    print(f"Mean doc confidence:     {report.mean_classification_confidence:.0%}")
    print(f"Doc classifications to review: {report.documents_requiring_classification_review}")
    print(f"Evidence extracted:      {report.evidence_total}")
    print(f"Evidence fields to review:     {report.evidence_requiring_review}")
    print(f"Criteria retrieved:      {report.criteria_total}")
    print(f"Supported:               {report.criteria_satisfied}")
    print(f"Not satisfied:           {report.criteria_not_satisfied}")
    print(f"Needs human review:      {report.criteria_needs_review}")
    print(f"  of which unstructured: {report.criteria_unstructured}")
    print(f"Missing evidence:        {report.criteria_missing}")
    print(f"Policy exclusions not evaluated: {report.policy_exclusions_not_evaluated}")
    print(f"Groundedness gate:       {report.groundedness_gate}")
    print(line)
    print(f"Readiness: {report.summary_line()}\n")

    print("Per-criterion trace")
    print(line)
    for ev in evaluations:
        icon = _ICON[ev.result]
        print(f"{icon} {ev.criterion_id}  {ev.criterion_description}")
        print(
            f"       result:     {ev.result.value} "
            f"({ev.evaluation_method.value}, conf {ev.confidence:.2f})"
        )
        print(f"       reason:     {ev.explanation}")
        # A fact assembled from several spans cites all of them, so the trace
        # shows every one — an evaluation is only as auditable as its weakest citation.
        cited = ev.patient_evidence_sources or (
            [ev.patient_evidence_source] if ev.patient_evidence_source else []
        )
        for index, p in enumerate(source for source in cited if source and source.filename):
            label = "evidence:  " if index == 0 else "  also:   "
            print(f'       {label} {p.filename} p.{p.page} — "{p.source_text}"')
        if ev.policy_source:
            p = ev.policy_source
            print(f"       policy src: {report.policy_id} p.{p.page}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RxAuth Milestone 0.")
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Print only the JSON output.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=get_settings().reports_dir,
        help="Destination directory (default: ./reports).",
    )
    args = parser.parse_args()

    case = build_case()
    policy = build_policy()
    report = run_pipeline(case, policy)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"case_{report.case_id}.json"
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    if args.json_only:
        print(report.model_dump_json(indent=2))
        return

    print_report(report, report.evaluations)
    print(
        f"Structured output written to: {out_path.relative_to(Path.cwd()) if out_path.is_relative_to(Path.cwd()) else out_path}"
    )


if __name__ == "__main__":
    main()
