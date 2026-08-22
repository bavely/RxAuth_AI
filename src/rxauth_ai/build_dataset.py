"""Reproducible synthetic document dataset builder (main README §7).

Generates fabricated, template-based text documents across the taxonomy in
`rxauth_ai.models.DocumentType` (pa_request, insurance_card, referral, prescription,
clinical_note, medication_history, lab_report, other). These stand in for the
text a real OCR/PDF-extraction step would produce, until that ingestion path
is built for real.

GUARDRAIL (main README §3): every patient, provider, payer, and drug name below
is a fabricated placeholder. No real PHI is used or referenced.

Deterministic: the same --seed and --per-class always produce byte-identical
output, so the dataset (and everything trained on it) is reproducible.

Usage:
    rxauth-build-dataset                    # default: 60 docs/class, seed 42
    rxauth-build-dataset --per-class 100 --seed 7
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from .models import DocumentType

# ---- synthetic vocabularies — all fabricated placeholders (README §3) ----
SYNTH_PATIENTS = [f"SYNTH-{i:04d}" for i in range(1, 300)]
SYNTH_PROVIDERS = ["Dr. A. Rivera", "Dr. M. Chen", "Dr. K. Osei", "Dr. L. Novak", "Dr. S. Ibrahim"]
SYNTH_PAYERS = [
    "Example Health Plan",
    "Sample Care Network",
    "Placeholder Insurance Co.",
    "Demo Health Partners",
]
SYNTH_DRUGS = ["Drug A", "Drug B", "Drug C", "Drug D"]
SYNTH_CONDITIONS = ["Example Condition", "Sample Syndrome", "Placeholder Disorder"]
SYNTH_DATES = [f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in (5, 14, 22)]
SYNTH_LAB_NAMES = ["A1c", "LDL cholesterol", "ALT", "eGFR", "CRP"]

SPLITS = ("train", "val", "test")
SPLIT_WEIGHTS = (0.70, 0.15, 0.15)
NOISE_RATE = 0.25  # fraction of docs that borrow one sentence from a random other class


def _p(rng: random.Random) -> str:
    return rng.choice(SYNTH_PATIENTS)


def _prov(rng: random.Random) -> str:
    return rng.choice(SYNTH_PROVIDERS)


def _payer(rng: random.Random) -> str:
    return rng.choice(SYNTH_PAYERS)


def _drug(rng: random.Random) -> str:
    return rng.choice(SYNTH_DRUGS)


def _cond(rng: random.Random) -> str:
    return rng.choice(SYNTH_CONDITIONS)


def _date(rng: random.Random) -> str:
    return rng.choice(SYNTH_DATES)


TEMPLATES: dict[str, list] = {
    DocumentType.PA_REQUEST.value: [
        lambda r: f"Prior Authorization Request for {_drug(r)}.",
        lambda r: f"Patient ID: {_p(r)}. Requested medication: {_drug(r)}.",
        lambda r: f"Prescriber: {_prov(r)}. Diagnosis: {_cond(r)}.",
        lambda r: (
            f"Health plan: {_payer(r)}. Please review the attached clinical documentation and approve coverage."
        ),
        lambda r: f"Quantity requested: 30-day supply. Date of request: {_date(r)}.",
        lambda r: (
            "This request is submitted in accordance with the plan's step-therapy and prior-authorization policy."
        ),
        lambda r: f"Diagnosis code on file for {_cond(r)}.",
        lambda r: (
            "Attached: chart notes, lab results, and medication history supporting this request."
        ),
    ],
    DocumentType.INSURANCE_CARD.value: [
        lambda r: f"{_payer(r)} Member Identification Card.",
        lambda r: f"Member ID: {_p(r)}. Group Number: GRP-{r.randint(1000, 9999)}.",
        lambda r: "RxBIN: 610014  RxPCN: EXPL  RxGRP: SAMPLE01.",
        lambda r: "Customer Service: 1-800-555-0100. Pharmacy Helpdesk: 1-800-555-0199.",
        lambda r: f"Plan type: PPO. Effective date: {_date(r)}.",
        lambda r: "In case of emergency call 911 or go to the nearest emergency room.",
        lambda r: "This card does not guarantee coverage. Refer to plan documents for details.",
    ],
    DocumentType.REFERRAL.value: [
        lambda r: f"Referral to specialty pharmacy program for {_cond(r)}.",
        lambda r: f"Referring provider: {_prov(r)}. Referred to: Specialty Clinic.",
        lambda r: f"Reason for referral: initiation of {_drug(r)} therapy.",
        lambda r: f"Patient: {_p(r)}. Please schedule an intake appointment.",
        lambda r: "Relevant history and prior treatment records are attached for review.",
        lambda r: f"Date of referral: {_date(r)}.",
        lambda r: "Please confirm receipt of this referral within 5 business days.",
    ],
    DocumentType.PRESCRIPTION.value: [
        lambda r: f"Rx: {_drug(r)}. Sig: take as directed.",
        lambda r: f"Prescriber: {_prov(r)}. DEA number on file.",
        lambda r: f"Patient: {_p(r)}. Date written: {_date(r)}.",
        lambda r: "Quantity: 30. Refills: 2. Dispense as written.",
        lambda r: f"Pharmacy: Example Specialty Pharmacy. NPI on file for {_prov(r)}.",
        lambda r: "Substitution permitted unless otherwise indicated.",
    ],
    DocumentType.CLINICAL_NOTE.value: [
        lambda r: f"Progress note for patient {_p(r)} seen by {_prov(r)}.",
        lambda r: f"Assessment: {_cond(r)}, stable on current regimen.",
        lambda r: f"Plan: continue {_drug(r)}, reassess at next follow-up visit.",
        lambda r: f"Subjective: patient reports symptoms consistent with {_cond(r)}.",
        lambda r: (
            f"Objective: vitals stable. {r.choice(SYNTH_LAB_NAMES)} reviewed and discussed with patient."
        ),
        lambda r: f"Visit date: {_date(r)}. Next follow-up in 8 weeks.",
        lambda r: "No acute distress noted on exam today.",
    ],
    DocumentType.MEDICATION_HISTORY.value: [
        lambda r: f"Medication history for {_p(r)}.",
        lambda r: f"{_drug(r)} — started {_date(r)}, discontinued due to inadequate response.",
        lambda r: f"{_drug(r)} — {r.randint(4, 24)} weeks of therapy documented.",
        lambda r: "Adherence confirmed via pharmacy fill records.",
        lambda r: f"Prior authorization on file with {_payer(r)} for prior therapy.",
        lambda r: "No known drug allergies reported.",
    ],
    DocumentType.LAB_REPORT.value: [
        lambda r: f"Laboratory Report for {_p(r)}.",
        lambda r: (
            f"{r.choice(SYNTH_LAB_NAMES)}: {r.uniform(4.0, 12.0):.1f} — collected {_date(r)}."
        ),
        lambda r: f"Ordering provider: {_prov(r)}.",
        lambda r: "Reference range and flag included per laboratory standard reporting.",
        lambda r: "Specimen type: venous blood draw.",
        lambda r: "Results reviewed and released to ordering provider.",
    ],
    DocumentType.OTHER.value: [
        lambda r: "General correspondence regarding account and billing inquiry.",
        lambda r: f"Appointment reminder for {_p(r)} on {_date(r)}.",
        lambda r: "Fax cover sheet. Number of pages: 3.",
        lambda r: f"Thank you letter from {_payer(r)} member services.",
        lambda r: "Office hours and holiday closure notice.",
    ],
}

LABELS = list(TEMPLATES.keys())

# Generic boilerplate that shows up across many real document types (cover
# sheets, footers, office-workflow notes). Mixing these in keeps the dataset
# from being trivially separable on class-unique tokens alone, which is more
# representative of real scanned documents than pure per-class vocabulary.
SHARED_POOL = [
    lambda r: "Please retain a copy of this document for your records.",
    lambda r: "Contact the office with any questions regarding this documentation.",
    lambda r: f"Case reference number: CASE-{r.randint(10000, 99999)}.",
    lambda r: "Document received and processed on file.",
    lambda r: f"On file with {_payer(r)}.",
    lambda r: f"Regarding patient {_p(r)}.",
]


def _generate_doc_text(label: str, rng: random.Random) -> str:
    pool = TEMPLATES[label]
    k = rng.randint(3, min(5, len(pool)))

    # Most of a document's content is generic boilerplate; only a couple of
    # sentences actually carry the class-distinguishing signal. Real scanned
    # documents work the same way — a form is mostly headers/footers/legal
    # text, with the identifying content in a small fraction of it.
    n_shared = rng.randint(1, min(3, k - 1))
    class_sentences = [tpl(rng) for tpl in rng.sample(pool, k - n_shared)]
    shared_sentences = [tpl(rng) for tpl in rng.sample(SHARED_POOL, n_shared)]
    sentences = class_sentences + shared_sentences

    if rng.random() < NOISE_RATE:
        other_label = rng.choice([lbl for lbl in LABELS if lbl != label])
        sentences.insert(rng.randrange(len(sentences) + 1), rng.choice(TEMPLATES[other_label])(rng))

    rng.shuffle(sentences)
    return " ".join(sentences) + "\n"


def _assign_splits(n: int, rng: random.Random) -> list[str]:
    n_train = round(n * SPLIT_WEIGHTS[0])
    n_val = round(n * SPLIT_WEIGHTS[1])
    assignment = ["train"] * n_train + ["val"] * n_val
    assignment += ["test"] * (n - len(assignment))
    rng.shuffle(assignment)
    return assignment


def build_dataset(out_dir: Path, per_class: int, seed: int) -> Path:
    """Generate the synthetic document dataset under `out_dir`.

    Returns the path to the written manifest.csv.
    """
    if per_class < 6:
        raise ValueError("per_class must be at least 6 so train, val, and test are non-empty.")

    doc_dir = out_dir / "documents"
    manifest_path = out_dir / "manifest.csv"

    rows = []
    doc_index = 0
    for label in LABELS:
        label_rng = random.Random(f"{seed}:{label}")
        splits = _assign_splits(per_class, label_rng)
        label_dir = doc_dir / label
        label_dir.mkdir(parents=True, exist_ok=True)
        for stale_document in label_dir.glob("doc_*.txt"):
            stale_document.unlink()

        for i in range(per_class):
            doc_rng = random.Random(f"{seed}:{label}:{i}")
            text = _generate_doc_text(label, doc_rng)
            filename = f"doc_{i:04d}.txt"
            (label_dir / filename).write_text(text, encoding="utf-8")

            doc_index += 1
            rows.append(
                {
                    "doc_id": f"SYN-{doc_index:05d}",
                    "filename": filename,
                    "relative_path": str((label_dir / filename).relative_to(out_dir)).replace(
                        "\\", "/"
                    ),
                    "label": label,
                    "split": splits[i],
                    "char_count": len(text),
                }
            )

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["doc_id", "filename", "relative_path", "label", "split", "char_count"]
        )
        writer.writeheader()
        writer.writerows(rows)

    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the synthetic document classification dataset."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="Destination directory (default: ./data).",
    )
    parser.add_argument(
        "--per-class",
        type=int,
        default=60,
        help="Documents to generate per class; minimum 6 (default: 60).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed (reproducibility).")
    args = parser.parse_args()

    manifest_path = build_dataset(args.output_dir, args.per_class, args.seed)
    total = args.per_class * len(LABELS)
    print(f"Generated {total} synthetic documents across {len(LABELS)} classes.")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
