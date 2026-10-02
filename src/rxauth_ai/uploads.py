"""Bounded, signature-aware staging for untrusted case documents."""

from __future__ import annotations

import codecs
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from .config import Settings

ALLOWED_SUFFIXES = frozenset(
    {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
)

MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
}


class UploadValidationError(ValueError):
    """An upload is unsupported, malformed, or exceeds a resource boundary."""


class UploadTooLargeError(UploadValidationError):
    pass


class UploadConflictError(UploadValidationError):
    pass


@dataclass(frozen=True)
class StagedUpload:
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    temporary_path: Path
    final_path: Path

    def commit(self) -> Path:
        os.replace(self.temporary_path, self.final_path)
        return self.final_path

    def discard(self) -> None:
        self.temporary_path.unlink(missing_ok=True)


def safe_filename(raw: str | None) -> str:
    filename = Path(raw or "document.txt").name
    if not filename or filename in {".", ".."}:
        raise UploadValidationError("The upload must have a valid filename.")
    if len(filename) > 255:
        raise UploadValidationError("The upload filename cannot exceed 255 characters.")
    if any(ord(character) < 32 for character in filename):
        raise UploadValidationError("The upload filename contains control characters.")
    return filename


def _validate_text(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if b"\x00" in chunk:
                raise UploadValidationError("Text uploads cannot contain NUL bytes.")
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                raise UploadValidationError("Text uploads must be valid UTF-8.") from exc
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise UploadValidationError("Text uploads must be valid UTF-8.") from exc


def _validate_pdf(path: Path, settings: Settings) -> None:
    with path.open("rb") as handle:
        if b"%PDF-" not in handle.read(1024):
            raise UploadValidationError("A .pdf upload must contain a PDF signature.")
    try:
        reader = PdfReader(path, strict=True)
        pages = len(reader.pages)
    except Exception as exc:
        raise UploadValidationError("The PDF is malformed or unreadable.") from exc
    if pages == 0:
        raise UploadValidationError("The PDF contains no pages.")
    if pages > settings.upload_max_pdf_pages:
        raise UploadValidationError(
            f"The PDF has {pages} pages; the limit is {settings.upload_max_pdf_pages}."
        )


def _expected_image_format(suffix: str) -> str:
    return {
        ".png": "PNG",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".tif": "TIFF",
        ".tiff": "TIFF",
        ".bmp": "BMP",
    }[suffix]


def _validate_image(path: Path, suffix: str, settings: Settings) -> None:
    try:
        with Image.open(path) as image:
            actual = image.format
            width, height = image.size
            frames = getattr(image, "n_frames", 1)
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise UploadValidationError("The image is malformed or unreadable.") from exc
    expected = _expected_image_format(suffix)
    if actual != expected:
        raise UploadValidationError(
            f"The file extension declares {expected}, but its signature is {actual or 'unknown'}."
        )
    pixels = width * height * frames
    if pixels > settings.upload_max_image_pixels:
        raise UploadValidationError(
            f"The image contains {pixels} decoded pixels; the limit is "
            f"{settings.upload_max_image_pixels}."
        )


def validate_staged_file(path: Path, suffix: str, settings: Settings) -> str:
    if suffix in {".txt", ".md"}:
        _validate_text(path)
    elif suffix == ".pdf":
        _validate_pdf(path, settings)
    else:
        _validate_image(path, suffix, settings)
    return MEDIA_TYPES[suffix]


def stage_upload(
    stream: BinaryIO,
    raw_filename: str | None,
    directory: Path,
    settings: Settings,
) -> StagedUpload:
    filename = safe_filename(raw_filename)
    suffix = Path(filename).suffix.casefold()
    if suffix not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        raise UploadValidationError(
            f"Unsupported file extension {suffix or '<none>'}. Allowed: {allowed}."
        )

    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / filename
    if final_path.exists():
        raise UploadConflictError(f"A document named {filename!r} already exists in this case.")
    temporary_path = directory / f".{uuid.uuid4().hex}.upload"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary_path.open("xb") as handle:
            while chunk := stream.read(settings.upload_chunk_bytes):
                size += len(chunk)
                if size > settings.upload_max_file_bytes:
                    raise UploadTooLargeError(
                        f"The upload exceeds the {settings.upload_max_file_bytes}-byte file limit."
                    )
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise UploadValidationError("Empty documents are not accepted.")
        media_type = validate_staged_file(temporary_path, suffix, settings)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return StagedUpload(
        filename=filename,
        media_type=media_type,
        size_bytes=size,
        sha256=digest.hexdigest(),
        temporary_path=temporary_path,
        final_path=final_path,
    )
