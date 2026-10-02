"""Reproducible synthetic document dataset builder (main README §7).

Generates fabricated, template-based documents across the taxonomy in
`rxauth_ai.models.DocumentType` (pa_request, insurance_card, referral, prescription,
clinical_note, medication_history, lab_report, other). The classification corpus
uses grouped template-family and case splits. A small companion ingestion corpus
renders deterministic PDFs and scan-like PNGs.

GUARDRAIL (main README §3): every patient, provider, payer, and drug name below
is a fabricated placeholder. No real PHI is used or referenced.

Deterministic: the same --seed and --per-class always produce byte-identical
output, so the dataset (and everything trained on it) is reproducible.

Usage:
    rxauth-build-dataset                    # 60 docs/class + rendered samples
    rxauth-build-dataset --per-class 100 --seed 7 --rendered-per-class 4
"""

from __future__ import annotations

import argparse
import csv
import random
import re
from pathlib import Path

from .config import get_settings
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

SPLITS = ("train", "val", "test", "challenge")
NOISE_RATE = 0.25  # fraction of docs that borrow one sentence from a random other class
FAMILY_COUNT = 10

# Families 0-6 train, 7 validation, 8 test, and 9 challenge. A family is never
# shared across splits, so the benchmark measures unseen document layouts rather
# than memorization of a layout observed during fitting.
FAMILY_SPLITS = {
    **{family: "train" for family in range(7)},
    7: "val",
    8: "test",
    9: "challenge",
}

FAMILY_FRAMES = [
    ("DOCUMENT INTAKE", "End of intake record", " | "),
    ("SPECIALTY WORKFLOW RECORD", "Confidential synthetic example", "\n"),
    ("TRANSMITTAL", "Generated for training only", " -- "),
    ("PATIENT SERVICES", "Synthetic record; not for care", "\n\n"),
    ("CLINICAL OPERATIONS", "Review status: pending", " / "),
    ("DOCUMENT CONTROL", "Archive copy", "\n"),
    ("CASE ATTACHMENT", "Attachment complete", " :: "),
    ("REVIEW PACKET", "Validation copy", "\n\n"),
    ("SOURCE DOCUMENT", "End of source document", " ~ "),
    ("SCANNED CORRESPONDENCE", "Image quality may affect recognition", "\n"),
]


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


def _apply_ocr_like_noise(text: str, rng: random.Random) -> str:
    substitutions = {"o": "0", "i": "1", "e": "c", "l": "1", "s": "5"}
    output: list[str] = []
    for character in text:
        replacement = substitutions.get(character.casefold())
        if replacement and rng.random() < 0.035:
            output.append(replacement.upper() if character.isupper() else replacement)
        else:
            output.append(character)
    return "".join(output)


def _generate_doc_text(label: str, rng: random.Random, family: int, case_index: int) -> str:
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

    noise_rate = 0.70 if FAMILY_SPLITS[family] == "challenge" else NOISE_RATE
    if rng.random() < noise_rate:
        other_label = rng.choice([lbl for lbl in LABELS if lbl != label])
        sentences.insert(rng.randrange(len(sentences) + 1), rng.choice(TEMPLATES[other_label])(rng))

    rng.shuffle(sentences)
    header, footer, separator = FAMILY_FRAMES[family]
    text = f"{header}\n{separator.join(sentences)}\n{footer}\n"
    # One synthetic identity belongs to exactly one case/split.
    text = re.sub(r"SYNTH-\d{4}", f"SYNTH-{case_index + 1:04d}", text)
    if FAMILY_SPLITS[family] == "challenge":
        text = _apply_ocr_like_noise(text, rng)
    return text


def build_dataset(
    out_dir: Path,
    per_class: int,
    seed: int,
    rendered_per_class: int = 0,
) -> Path:
    """Generate the synthetic document dataset under `out_dir`.

    Returns the path to the written manifest.csv.
    """
    if per_class < FAMILY_COUNT:
        raise ValueError(
            f"per_class must be at least {FAMILY_COUNT} so every grouped split is non-empty."
        )
    if not 0 <= rendered_per_class <= per_class:
        raise ValueError("rendered_per_class must be between 0 and per_class.")

    doc_dir = out_dir / "documents"
    asset_dir = out_dir / "rendered"
    manifest_path = out_dir / "manifest.csv"
    ingestion_manifest_path = out_dir / "ingestion_manifest.csv"

    rows = []
    ingestion_rows = []
    doc_index = 0
    for label in LABELS:
        label_dir = doc_dir / label
        label_dir.mkdir(parents=True, exist_ok=True)
        rendered_label_dir = asset_dir / label
        rendered_label_dir.mkdir(parents=True, exist_ok=True)
        for stale_document in label_dir.glob("doc_*.*"):
            stale_document.unlink()
        for stale_document in rendered_label_dir.glob("doc_*.*"):
            stale_document.unlink()

        for i in range(per_class):
            family = i % FAMILY_COUNT
            split = FAMILY_SPLITS[family]
            doc_rng = random.Random(f"{seed}:{label}:{i}")
            text = _generate_doc_text(label, doc_rng, family, i)
            filename = f"doc_{i:04d}.txt"
            text_path = label_dir / filename
            text_path.write_text(text, encoding="utf-8", newline="\n")

            doc_index += 1
            rows.append(
                {
                    "doc_id": f"SYN-{doc_index:05d}",
                    "filename": filename,
                    "relative_path": str((label_dir / filename).relative_to(out_dir)).replace(
                        "\\", "/"
                    ),
                    "label": label,
                    "split": split,
                    "char_count": len(text),
                    "case_id": f"SYNTH-CASE-{i + 1:04d}",
                    "template_family_id": f"family-{family:02d}",
                    "degradation": "ocr_like_text" if split == "challenge" else "none",
                }
            )

            if i < rendered_per_class:
                from .rendering import render_text_image, render_text_pdf

                degradation = ("clean", "rotated", "blurred", "low_contrast", "noisy")[i % 5]
                pdf_path = rendered_label_dir / f"doc_{i:04d}.pdf"
                image_path = rendered_label_dir / f"doc_{i:04d}_{degradation}.png"
                render_text_pdf(text, pdf_path)
                render_text_image(
                    text,
                    image_path,
                    degradation=degradation,
                    seed=int.from_bytes(f"{seed}:{label}:{i}".encode(), "little") % (2**32),
                )
                for source_format, asset_path, applied_degradation in (
                    ("pdf", pdf_path, "clean"),
                    ("image", image_path, degradation),
                ):
                    ingestion_rows.append(
                        {
                            "doc_id": f"SYN-{doc_index:05d}",
                            "label": label,
                            "case_id": f"SYNTH-CASE-{i + 1:04d}",
                            "template_family_id": f"family-{family:02d}",
                            "source_format": source_format,
                            "degradation": applied_degradation,
                            "asset_relative_path": str(asset_path.relative_to(out_dir)).replace(
                                "\\", "/"
                            ),
                            "ground_truth_relative_path": str(
                                text_path.relative_to(out_dir)
                            ).replace("\\", "/"),
                        }
                    )

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "doc_id",
                "filename",
                "relative_path",
                "label",
                "split",
                "char_count",
                "case_id",
                "template_family_id",
                "degradation",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    with ingestion_manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "doc_id",
                "label",
                "case_id",
                "template_family_id",
                "source_format",
                "degradation",
                "asset_relative_path",
                "ground_truth_relative_path",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(ingestion_rows)

    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the synthetic document classification dataset."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=get_settings().data_dir,
        help="Destination directory (default: ./data).",
    )
    parser.add_argument(
        "--per-class",
        type=int,
        default=60,
        help="Documents to generate per class; minimum 10 (default: 60).",
    )
    parser.add_argument(
        "--rendered-per-class",
        type=int,
        default=2,
        help="Documents per class to also render as PDF and PNG (default: 2).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed (reproducibility).")
    args = parser.parse_args()

    manifest_path = build_dataset(
        args.output_dir,
        args.per_class,
        args.seed,
        rendered_per_class=args.rendered_per_class,
    )
    total = args.per_class * len(LABELS)
    print(f"Generated {total} synthetic documents across {len(LABELS)} classes.")
    print(f"Rendered {args.rendered_per_class * len(LABELS) * 2} PDF/image samples.")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
