"""Issue 0009-GATE: the scan-quality gate. Given OCR text (+ optional image metrics / docling confidence),
classify a page readable / degraded / unreadable, so a degraded page can be ESCALATED to a VLM and a genuinely
unreadable one flagged PARTIAL rather than ingested as gibberish. Thresholds are validated in the OCR benchmark
(docs/eval/ocr_benchmark.md): Laplacian var clean 2440 / moderate 549 / heavy 54; dark_frac 0.03 / 0.028 /
0.003; garbage OCR has a near-zero common-word hit rate.
"""
from __future__ import annotations

import numpy as np

from rag_wright.capabilities.scan_quality import (
    ScanQuality,
    assess_document,
    assess_scan,
    image_quality,
    text_readability,
)

_REAL = ("Supplier's total aggregate liability arising out of or in connection with this Agreement, whether in "
         "contract, tort including negligence, breach of statutory duty or otherwise, shall not exceed the total "
         "fees paid by the Customer in the twelve months preceding the claim.")
_GARBAGE = ("upareaia aes jo sa ayo worse eas ss Pesioperann dae esp Al sone stm JO TPE cer ecm snd women pe at "
            "pe mecpusaag Ape om Hp ae SOS agg po pened hg omens tapi")


# --- text readability (common-word hit rate) ---------------------------------------------------

def test_real_text_reads_high_garbage_reads_low():
    assert text_readability(_REAL) > 0.20          # real prose is dense with common words
    assert text_readability(_GARBAGE) < 0.05       # OCR garbage almost never hits common words
    assert text_readability("") == 0.0


# --- image quality metrics (cv2) ---------------------------------------------------------------

def test_image_quality_separates_sharp_from_blurry():
    rng = np.random.default_rng(0)
    sharp = rng.integers(0, 255, (400, 400), dtype=np.uint8)          # high-frequency content -> high laplacian
    blurry = np.full((400, 400), 240, np.uint8)                       # flat/washed-out -> ~0 laplacian, low dark
    qs, qb = image_quality(sharp), image_quality(blurry)
    assert qs["laplacian_var"] > qb["laplacian_var"]
    assert qb["dark_frac"] < 0.01                                     # washed-out page has almost no dark ink


# --- the verdict --------------------------------------------------------------------------------

def test_garbage_ocr_text_is_degraded():
    a = assess_scan(_GARBAGE)
    assert a.quality is ScanQuality.DEGRADED and "word" in a.reason.lower()


def test_real_ocr_text_is_readable():
    assert assess_scan(_REAL).quality is ScanQuality.READABLE


def test_blurry_image_is_degraded_even_before_text():
    # heavy-scan image metrics (Laplacian ~54, dark_frac ~0.003) -> degraded regardless of any text
    a = assess_scan(None, laplacian_var=54.0, dark_frac=0.003)
    assert a.quality is ScanQuality.DEGRADED and ("blur" in a.reason.lower() or "faint" in a.reason.lower())


def test_readable_image_metrics_pass():
    a = assess_scan(_REAL, laplacian_var=2440.0, dark_frac=0.03)
    assert a.quality is ScanQuality.READABLE


def test_low_docling_confidence_is_degraded():
    a = assess_scan(_REAL, confidence=0.2)
    assert a.quality is ScanQuality.DEGRADED and "confidence" in a.reason.lower()


def test_empty_text_with_no_signals_is_unreadable():
    # nothing recognised and no other signal -> the strongest verdict (a blank/failed page)
    assert assess_scan("").quality is ScanQuality.UNREADABLE


def test_assess_document_folds_in_image_metrics_0009_gate_cal():
    # 0009-GATE-CAL: a page whose OCR TEXT looks readable but whose IMAGE is blurred/faded -> DEGRADED. This is
    # the case a text-only gate misses (garbled-but-common-word OCR passes the word-hit threshold).
    from types import SimpleNamespace

    class _Doc:
        def iterate_items(self):
            return [(SimpleNamespace(
                text="shall not exceed the total fees paid by the party under this agreement",
                prov=[SimpleNamespace(page_no=1)]), 0)]

    blurry = np.full((300, 300), 245, np.uint8)              # flat/faded -> laplacian ~0, dark_frac ~0
    assert assess_document(_Doc(), page_images={1: blurry})[1].quality is ScanQuality.DEGRADED
    assert assess_document(_Doc())[1].quality is ScanQuality.READABLE  # same text, no image -> passes
