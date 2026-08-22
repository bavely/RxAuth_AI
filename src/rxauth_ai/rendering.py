"""Deterministic synthetic PDF/image rendering for the ingestion benchmark."""

from __future__ import annotations

import random
import textwrap
from pathlib import Path
from typing import Literal

Degradation = Literal["clean", "rotated", "blurred", "low_contrast", "noisy"]


def render_text_pdf(text: str, path: Path) -> None:
    """Render a deterministic, text-bearing PDF that pypdf can parse."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen.canvas import Canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(path), pagesize=letter, invariant=1, pageCompression=0)
    _, height = letter
    text_object = canvas.beginText(54, height - 54)
    text_object.setFont("Helvetica", 10)
    for paragraph in text.splitlines() or [text]:
        for line in textwrap.wrap(paragraph, width=92) or [""]:
            if text_object.getY() < 54:
                canvas.drawText(text_object)
                canvas.showPage()
                text_object = canvas.beginText(54, height - 54)
                text_object.setFont("Helvetica", 10)
            text_object.textLine(line)
    canvas.drawText(text_object)
    canvas.save()


def render_text_image(
    text: str,
    path: Path,
    *,
    degradation: Degradation = "clean",
    seed: int = 42,
) -> None:
    """Render text to an image and apply a reproducible scan degradation."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [line for paragraph in text.splitlines() for line in textwrap.wrap(paragraph, 76)]
    lines = lines or [""]
    image = Image.new("L", (1000, max(420, 90 + len(lines) * 28)), color=255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18)
    y = 45
    for line in lines:
        draw.text((50, y), line, fill=20, font=font)
        y += 28

    if degradation == "rotated":
        angle = random.Random(seed).choice((-2.4, -1.6, 1.7, 2.5))
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    elif degradation == "blurred":
        image = image.filter(ImageFilter.GaussianBlur(radius=1.2))
    elif degradation == "low_contrast":
        image = ImageEnhance.Contrast(image).enhance(0.42)
    elif degradation == "noisy":
        values = np.array(image, dtype=np.int16)
        noise = np.random.default_rng(seed).normal(0, 18, values.shape)
        values = np.clip(values + noise, 0, 255).astype(np.uint8)
        image = Image.fromarray(values, mode="L")
    elif degradation != "clean":
        raise ValueError(f"Unknown degradation: {degradation}")

    encoded = cv2.imencode(".png", np.array(image), [cv2.IMWRITE_PNG_COMPRESSION, 9])[1]
    path.write_bytes(encoded.tobytes())
