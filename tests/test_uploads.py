"""Security and resource-boundary tests for untrusted uploads."""

from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from rxauth_ai.case_jobs import cleanup_expired_temporary_copies
from rxauth_ai.config import settings_from_env
from rxauth_ai.uploads import (
    UploadTooLargeError,
    UploadValidationError,
    stage_upload,
)


def _image_bytes(image_format: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format=image_format)
    return output.getvalue()


def _pdf_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "valid.pdf"
    canvas = Canvas(str(path), invariant=1)
    canvas.drawString(20, 20, "Synthetic prior authorization")
    canvas.save()
    return path.read_bytes()


@pytest.mark.parametrize(
    ("filename", "content", "expected_media_type"),
    [
        ("note.txt", b"Diagnosis: synthetic", "text/plain"),
        ("note.md", b"# Synthetic note", "text/markdown"),
        ("scan.png", _image_bytes("PNG"), "image/png"),
        ("scan.jpg", _image_bytes("JPEG"), "image/jpeg"),
        ("scan.jpeg", _image_bytes("JPEG"), "image/jpeg"),
        ("scan.tif", _image_bytes("TIFF"), "image/tiff"),
        ("scan.tiff", _image_bytes("TIFF"), "image/tiff"),
        ("scan.bmp", _image_bytes("BMP"), "image/bmp"),
    ],
)
def test_existing_upload_formats_are_signature_validated(
    tmp_path, filename, content, expected_media_type
):
    staged = stage_upload(io.BytesIO(content), filename, tmp_path / "case", settings_from_env())

    assert staged.media_type == expected_media_type
    assert staged.size_bytes == len(content)
    assert staged.temporary_path.is_file()
    assert staged.commit().is_file()


def test_a_valid_pdf_is_accepted(tmp_path):
    content = _pdf_bytes(tmp_path)

    staged = stage_upload(io.BytesIO(content), "packet.pdf", tmp_path / "case", settings_from_env())

    assert staged.media_type == "application/pdf"


def test_an_extension_cannot_disguise_another_file_type(tmp_path):
    with pytest.raises(UploadValidationError, match="signature"):
        stage_upload(
            io.BytesIO(_image_bytes("PNG")),
            "disguised.jpg",
            tmp_path / "case",
            settings_from_env(),
        )


def test_binary_content_cannot_be_uploaded_as_text(tmp_path):
    with pytest.raises(UploadValidationError, match="NUL"):
        stage_upload(
            io.BytesIO(b"valid prefix\x00binary"),
            "note.txt",
            tmp_path / "case",
            settings_from_env(),
        )


def test_the_stream_stops_at_the_file_limit_and_removes_its_temporary_file(tmp_path):
    directory = tmp_path / "case"
    settings = settings_from_env(upload_max_file_bytes=1024, upload_chunk_bytes=64 * 1024)

    with pytest.raises(UploadTooLargeError):
        stage_upload(io.BytesIO(b"x" * 1025), "note.txt", directory, settings)

    assert list(directory.glob("*.upload")) == []


def test_an_empty_or_unsupported_document_is_rejected(tmp_path):
    with pytest.raises(UploadValidationError, match="Empty"):
        stage_upload(io.BytesIO(b""), "note.txt", tmp_path / "empty", settings_from_env())
    with pytest.raises(UploadValidationError, match="Unsupported"):
        stage_upload(io.BytesIO(b"MZ"), "malware.exe", tmp_path / "exe", settings_from_env())


def test_temporary_case_copies_expire_after_72_hours(tmp_path):
    settings = settings_from_env(
        artifacts_dir=tmp_path / "artifacts", temporary_copy_retention_hours=72
    )
    expired = settings.artifacts_dir / "cases" / "org-a" / "expired"
    fresh = settings.artifacts_dir / "cases" / "org-a" / "fresh"
    expired.mkdir(parents=True)
    fresh.mkdir(parents=True)
    (expired / "case.json").write_text("{}", encoding="utf-8")
    old = datetime(2026, 8, 29, tzinfo=timezone.utc).timestamp()
    os.utime(expired, (old, old))

    removed = cleanup_expired_temporary_copies(
        settings, now=datetime(2026, 9, 2, tzinfo=timezone.utc)
    )

    assert removed == 1
    assert not expired.exists()
    assert fresh.is_dir()
