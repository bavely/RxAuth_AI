"""Document ingestion and image preprocessing for Phase 1.5.

The ingestion boundary returns page-level text with the method and confidence
that produced it. Text files and text PDFs work without an OCR engine. Images
are normalized with OpenCV and sent to an injectable OCR backend; the default
backend uses Tesseract when the optional dependency and system binary exist.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class IngestionError(RuntimeError):
    """Raised when a supported document cannot be ingested safely."""


class OCRUnavailableError(IngestionError):
    """Raised when image ingestion is requested without an OCR runtime."""


class IngestedPage(BaseModel):
    page_number: int = Field(ge=1)
    text: str
    extraction_method: Literal["text", "pypdf", "ocr"]
    confidence: float = Field(ge=0.0, le=1.0)


class IngestedDocument(BaseModel):
    filename: str
    media_type: Literal["text", "pdf", "image"]
    pages: list[IngestedPage]
    preprocessing: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text.strip() for page in self.pages if page.text.strip())

    @property
    def mean_confidence(self) -> float:
        if not self.pages:
            return 0.0
        return sum(page.confidence for page in self.pages) / len(self.pages)


OCRBackend = Callable[[Any], tuple[str, float]]

_TEXT_SUFFIXES = {".txt", ".md"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def _deskew(image: Any, cv2: Any, np: Any) -> Any:
    rows, columns = np.where(image < 250)
    coordinates = np.column_stack((columns, rows))
    if len(coordinates) < 20:
        return image

    angle = cv2.minAreaRect(coordinates)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.05:
        return image

    height, width = image.shape[:2]
    center = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )


def preprocess_image(path: Path) -> Any:
    """Deskew, denoise, and binarize an image for OCR."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - exercised only in minimal installs
        raise IngestionError(
            "Image preprocessing requires the ingestion dependencies; install the project first."
        ) from exc

    raw = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise IngestionError(f"Could not decode image: {path}")

    denoised = cv2.fastNlMeansDenoising(
        image, None, h=10, templateWindowSize=7, searchWindowSize=21
    )
    deskewed = _deskew(denoised, cv2, np)
    return cv2.threshold(deskewed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def _tesseract_backend(image: Any) -> tuple[str, float]:
    try:
        from importlib import import_module

        pytesseract = import_module("pytesseract")
        output = pytesseract.Output
    except ImportError as exc:
        raise OCRUnavailableError(
            "Image OCR requires the optional `ocr` dependency and a Tesseract system binary. "
            "Install both, or pass an OCR backend explicitly."
        ) from exc

    try:
        data = pytesseract.image_to_data(image, output_type=output.DICT)
    except Exception as exc:  # noqa: BLE001 - normalize backend/system failures
        raise OCRUnavailableError(
            "Tesseract could not process the image. Verify that its system binary is installed "
            "and available on PATH."
        ) from exc

    words: list[str] = []
    confidences: list[float] = []
    for word, raw_confidence in zip(data["text"], data["conf"], strict=True):
        word = word.strip()
        if not word:
            continue
        words.append(word)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if confidence >= 0:
            confidences.append(confidence / 100.0)

    text = " ".join(words)
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return text, confidence


def ingest_document(path: Path, *, ocr_backend: OCRBackend | None = None) -> IngestedDocument:
    """Extract normalized page text from a text file, PDF, or image."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    suffix = path.suffix.casefold()
    if suffix in _TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8")
        return IngestedDocument(
            filename=path.name,
            media_type="text",
            pages=[
                IngestedPage(
                    page_number=1,
                    text=text,
                    extraction_method="text",
                    confidence=1.0,
                )
            ],
        )

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - exercised only in minimal installs
            raise IngestionError("PDF ingestion requires pypdf.") from exc

        reader = PdfReader(path)
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(
                IngestedPage(
                    page_number=index,
                    text=text,
                    extraction_method="pypdf",
                    confidence=1.0 if text.strip() else 0.0,
                )
            )
        if not any(page.text.strip() for page in pages):
            raise IngestionError(
                "The PDF has no extractable text. Rasterize its pages and use the image OCR path."
            )
        return IngestedDocument(filename=path.name, media_type="pdf", pages=pages)

    if suffix in _IMAGE_SUFFIXES:
        normalized = preprocess_image(path)
        text, confidence = (ocr_backend or _tesseract_backend)(normalized)
        return IngestedDocument(
            filename=path.name,
            media_type="image",
            preprocessing=["grayscale", "denoise", "deskew", "otsu_threshold"],
            pages=[
                IngestedPage(
                    page_number=1,
                    text=text,
                    extraction_method="ocr",
                    confidence=confidence,
                )
            ],
        )

    raise IngestionError(f"Unsupported document extension: {suffix or '<none>'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract normalized text from a document.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(ingest_document(args.path).model_dump(), indent=2))


if __name__ == "__main__":
    main()
