"""Tests for Phase 1.5 document ingestion and synthetic rendering."""

from __future__ import annotations

import tempfile
from pathlib import Path

from rxauth_ai.benchmark_ingestion import benchmark_ingestion, character_error_rate
from rxauth_ai.ingestion import ingest_document, preprocess_image
from rxauth_ai.rendering import render_text_image, render_text_pdf

SYNTHETIC_TEXT = "Prior Authorization Request\nPatient ID: SYNTH-0001\nDrug A requested."


def test_ingests_text_with_page_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "request.txt"
        path.write_text(SYNTHETIC_TEXT, encoding="utf-8")

        document = ingest_document(path)

        assert document.media_type == "text"
        assert document.pages[0].page_number == 1
        assert document.pages[0].confidence == 1.0
        assert "SYNTH-0001" in document.text


def test_renders_and_ingests_text_pdf():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "request.pdf"
        render_text_pdf(SYNTHETIC_TEXT, path)

        document = ingest_document(path)

        assert document.media_type == "pdf"
        assert document.pages[0].extraction_method == "pypdf"
        assert "Prior Authorization Request" in document.text


def test_image_preprocessing_and_injected_ocr_backend_are_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp) / "scan-a.png"
        second = Path(tmp) / "scan-b.png"
        render_text_image(SYNTHETIC_TEXT, first, degradation="noisy", seed=11)
        render_text_image(SYNTHETIC_TEXT, second, degradation="noisy", seed=11)
        assert first.read_bytes() == second.read_bytes()

        normalized = preprocess_image(first)
        assert len(normalized.shape) == 2
        assert set(normalized.flatten()).issubset({0, 255})

        document = ingest_document(first, ocr_backend=lambda _image: (SYNTHETIC_TEXT, 0.87))
        assert document.media_type == "image"
        assert document.pages[0].extraction_method == "ocr"
        assert document.mean_confidence == 0.87
        assert document.preprocessing == ["grayscale", "denoise", "deskew", "otsu_threshold"]


def test_dataset_builder_creates_pdf_and_image_ingestion_manifest():
    from rxauth_ai.build_dataset import build_dataset

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        build_dataset(out_dir, per_class=10, seed=3, rendered_per_class=1)

        manifest = (out_dir / "ingestion_manifest.csv").read_text(encoding="utf-8")
        assert "source_format,degradation,asset_relative_path" in manifest
        assert len(list((out_dir / "rendered").glob("*/*.pdf"))) == 8
        assert len(list((out_dir / "rendered").glob("*/*.png"))) == 8

        results = benchmark_ingestion(out_dir, ocr_backend=lambda _image: (SYNTHETIC_TEXT, 0.90))
        assert results["pdf_documents"] == 8
        assert results["image_documents"] == 8
        assert results["image_preprocessing_success_rate"] == 1.0
        assert results["ocr_documents"] == 8


def test_character_error_rate_contract():
    assert character_error_rate("same text", "same   text") == 0
    assert character_error_rate("abc", "axc") == 1 / 3
