"""OCR-EVAL / issue 0009: classical (CPU, no GPU) image preprocessing for degraded scans.

Renders a PDF's pages to images and cleans each one -- grayscale -> denoise -> deskew -> binarize -- then
writes a preprocessed PDF the OCR benchmark can run through the same docling backends. The question 0009-PREP-AB
answers: does classical preprocessing lift a HEAVY (garbage-OCR) scan back toward the readable ~1.000?

Two binarization methods:
  - basic:   OpenCV adaptive Gaussian threshold
  - sauvola: scikit-image Sauvola local threshold (the document-degradation gold standard)
Everything is OpenCV/NumPy/scikit-image on CPU; no GPU, no model.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image


def render_pages(pdf: Path, dpi: int = 200) -> list[np.ndarray]:
    """Render each PDF page to a grayscale numpy image at `dpi`."""
    doc = pdfium.PdfDocument(str(pdf))
    scale = dpi / 72.0
    out = []
    for i in range(len(doc)):
        pil = doc[i].render(scale=scale).to_pil().convert("L")
        out.append(np.asarray(pil))
    return out


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Estimate skew from the text pixels (min-area rect) and rotate upright. Small guarded correction only."""
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 200:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.3 or abs(angle) > 20:  # negligible or implausible -> leave as-is
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def preprocess(gray: np.ndarray, method: str = "sauvola") -> np.ndarray:
    """Clean one grayscale page: denoise -> deskew -> binarize (method)."""
    den = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
    dsk = _deskew(den)
    if method == "basic":
        return cv2.adaptiveThreshold(dsk, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    if method == "sauvola":
        from skimage.filters import threshold_sauvola

        t = threshold_sauvola(dsk, window_size=25)
        return ((dsk > t) * 255).astype(np.uint8)
    if method == "enhance":  # NO binarization: pull up faded/low-contrast text (CLAHE) + counter blur (unsharp)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        en = clahe.apply(dsk)
        blur = cv2.GaussianBlur(en, (0, 0), 3)
        return cv2.addWeighted(en, 1.6, blur, -0.6, 0)  # unsharp mask
    raise ValueError(f"unknown preprocess method: {method}")


def write_pdf(images: list[np.ndarray], path: Path, dpi: int = 200) -> Path:
    """Write cleaned page images back into a multi-page PDF the OCR pipeline can consume."""
    pils = [Image.fromarray(im).convert("L") for im in images]
    pils[0].save(str(path), save_all=True, append_images=pils[1:], resolution=float(dpi))
    return path


def preprocess_pdf(pdf: Path, out: Path, method: str = "sauvola", dpi: int = 200) -> Path:
    """Render -> preprocess -> write a cleaned PDF. Returns the output path."""
    pages = [preprocess(g, method) for g in render_pages(pdf, dpi)]
    return write_pdf(pages, out, dpi)
