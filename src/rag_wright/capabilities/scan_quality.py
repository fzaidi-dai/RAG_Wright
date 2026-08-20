"""Issue 0009-GATE: the scan-quality gate.

Decide whether a scanned page's OCR is trustworthy, so the tiered OCR path can react: a DEGRADED page is
escalated to a VLM (which reads blurred/faded text by language context), and a genuinely UNREADABLE page is
flagged PARTIAL / needs-rescan rather than ingested as gibberish (the 0006-C / ENG-1 lossless principle applied
to OCR).

Signals (any subset; the strongest DEGRADED/UNREADABLE verdict wins):
  - `text_readability`  -- common-word hit rate of the OCR text. Garbage OCR ("upareaia aes jo sa ...") almost
    never hits common English words; real prose is dense with them. The strongest post-OCR signal, no dep.
  - `image_quality`     -- Laplacian variance (blur) + dark_frac (faint ink), pre-OCR, via OpenCV. Predicts an
    unreadable page before OCR is even run.
  - docling `confidence` -- the parser's own per-page `PageConfidenceScores` (0..1), when available.

Thresholds are the ones separated in the OCR benchmark (docs/eval/ocr_benchmark.md): clean/moderate scans read
at word-hit ~0.2-0.4, Laplacian 2440/549, dark_frac 0.03/0.028; the heavy scan that defeats all OCR sits at
word-hit ~0, Laplacian 54, dark_frac 0.003.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel

# validated thresholds (see docs/eval/ocr_benchmark.md)
_WORD_HIT_DEGRADED = 0.08       # readable text >> this; garbage OCR ~ 0
_LAPLACIAN_DEGRADED = 150.0     # heavy 54 (blur) vs moderate 549 / clean 2440
_DARK_FRAC_FAINT = 0.008        # heavy 0.003 (almost no ink) vs ~0.03 readable
_CONFIDENCE_DEGRADED = 0.5      # docling PageConfidenceScores below this -> distrust

# a small, dependency-free set of the most common English words -- their hit rate in OCR text cleanly separates
# real prose (dense with these) from OCR garbage (almost none). Deliberately generic, not domain-specific.
_COMMON_WORDS = frozenset((
    "the of and to a in that is was he for it with as his on be at by i this had not are but from or have an "
    "they which one you were her all she there would their we him been has when who will more no if out so said "
    "what up its about into than them can only other new some could time these two may then do first any my now "
    "such like our over me after also did many shall not any such under upon herein hereof any all each party "
    "parties agreement pursuant provided further including without between whether"
).split())


class ScanQuality(str, Enum):
    READABLE = "readable"        # trust the OCR text
    DEGRADED = "degraded"        # low-quality -> escalate to a VLM
    UNREADABLE = "unreadable"    # nothing recovered -> flag PARTIAL / needs-rescan


class ScanAssessment(BaseModel):
    quality: ScanQuality
    reason: str
    word_hit_rate: float | None = None
    laplacian_var: float | None = None
    dark_frac: float | None = None
    confidence: float | None = None


def text_readability(text: str) -> float:
    """Fraction of alphabetic OCR tokens that are common English words (0..1). ~0 for OCR garbage."""
    toks = re.findall(r"[A-Za-z]{2,}", (text or "").lower())
    if not toks:
        return 0.0
    return sum(t in _COMMON_WORDS for t in toks) / len(toks)


def image_quality(gray) -> dict:
    """Pre-OCR page metrics from a grayscale image (numpy 2-D uint8): Laplacian variance (blur; higher = sharper)
    and dark_frac (fraction of ink-dark pixels; a faint/washed-out scan is near zero)."""
    import cv2
    import numpy as np

    g = np.asarray(gray)
    return {"laplacian_var": float(cv2.Laplacian(g, cv2.CV_64F).var()), "dark_frac": float((g < 128).mean())}


def assess_scan(text: str | None, *, laplacian_var: float | None = None, dark_frac: float | None = None,
                confidence: float | None = None) -> ScanAssessment:
    """Classify a page from whatever signals are available. The strongest negative verdict wins; if nothing at
    all is recognised and no other signal is present, the page is UNREADABLE (a blank / failed scan)."""
    whr = text_readability(text) if text is not None else None

    def _a(q: ScanQuality, reason: str) -> ScanAssessment:
        return ScanAssessment(quality=q, reason=reason, word_hit_rate=whr, laplacian_var=laplacian_var,
                              dark_frac=dark_frac, confidence=confidence)

    # UNREADABLE: an empty/near-empty result with no positive evidence anywhere
    if text is not None and not (text or "").strip() and laplacian_var is None and confidence is None:
        return _a(ScanQuality.UNREADABLE, "no text recognised")

    # DEGRADED signals (escalate to a VLM)
    if whr is not None and (text or "").strip() and whr < _WORD_HIT_DEGRADED:
        return _a(ScanQuality.DEGRADED, f"low word-hit-rate {whr:.3f} (likely garbage OCR)")
    if laplacian_var is not None and laplacian_var < _LAPLACIAN_DEGRADED:
        return _a(ScanQuality.DEGRADED, f"blurry image (laplacian_var {laplacian_var:.0f})")
    if dark_frac is not None and dark_frac < _DARK_FRAC_FAINT:
        return _a(ScanQuality.DEGRADED, f"faint image (dark_frac {dark_frac:.3f})")
    if confidence is not None and confidence < _CONFIDENCE_DEGRADED:
        return _a(ScanQuality.DEGRADED, f"low OCR confidence {confidence:.2f}")

    return _a(ScanQuality.READABLE, "ok")


def register_scan_quality(registry) -> None:
    """0009-GATE: register `scan_quality` (function; page signals -> a readable/degraded/unreadable verdict)."""
    registry.register("scan_quality", contract=ScanAssessment, kind="function",
                      display_name="Scan-quality gate (route degraded scans to VLM; flag unreadable PARTIAL)")
