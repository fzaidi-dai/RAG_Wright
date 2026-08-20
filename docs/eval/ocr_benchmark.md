# OCR benchmark — engine speed & accuracy for scanned documents

Harness: `scripts/ocr_benchmark.py` (extensible backend registry; per-page or whole-doc ground truth; graceful
skip of uninstalled engines; JSON + accuracy-matrix output). Benchmark-only OCR deps live in the isolated
`ocr-bench` extra (`ocrmac`, `mlx-vlm`, `onnxruntime`) so the cross-platform core is untouched.

Run:
```
uv run --no-sync python -m scripts.ocr_benchmark --corpus <dir>            # whole-doc <stem>.ground-truth.txt
uv run --no-sync python -m scripts.ocr_benchmark --corpus temp/ocr_test    # per-page page_N.txt
uv run --no-sync python -m scripts.ocr_benchmark --backends tesseract ocrmac granite_docling_mlx
```

## Motivation

Reported symptom: docling's default OCR (RapidOCR) is "~60s/page, too slow." Investigation reframed the problem
to **accuracy on degraded scans**, and showed the speed figure is not reproducible per-page on macOS.

## Results — RuleWright fixtures (same 10-page contract at 3 scan-degradation levels, EXACT ground truth)

char_sim (difflib ratio vs exact GT; 1.0 = identical) @ seconds/page, macOS (Apple Silicon), model caches warm:

| engine | clean | moderate | heavy |
|---|---|---|---|
| docling_default (= Apple Vision on macOS) | 1.000 @ 0.82s | 1.000 @ 1.07s | **0.032** @ 1.15s |
| ocrmac (Apple Vision) | 1.000 @ 0.77s | 1.000 @ 1.08s | **0.032** @ 1.07s |
| tesseract (CLI) | 1.000 @ 1.55s | 1.000 @ 1.70s | **0.006** @ 1.54s |
| rapidocr (CPU/onnx) | 1.000 @ 1.75s | 0.989 @ 1.55s | **0.102** @ 1.32s |
| granite_docling_mlx (VLM) | 1.000 @ 4.33s | 1.000 @ 4.25s | **0.023** @ **35.63s** |

Earlier per-page run on the missouri notice (150 DPI, *rough* GT) had all engines ~0.76–0.81 — that was the
rough GT's ceiling, not OCR error; with exact GT (above) readable-scan OCR is near-perfect.

## Findings

1. **On readable scans (clean + moderate), every engine is ~perfect (~1.000).** Accuracy is NOT differentiated
   by engine choice — swapping the OCR engine does nothing for accuracy on normal scans.
2. **On the heavy (severely degraded) scan, everything fails — including the VLM.** Traditional OCR emits
   garbage (`'upareaia aes jo sa ayo worse...'` where GT is `MASTER SERVICES AGREEMENT / 1. LIMITATION OF
   LIABILITY`), char_sim 0.006–0.10; Granite-Docling also fails (0.023) AND slows 8× (35.6s/page, struggling on
   the noise). No OCR engine — traditional or VLM — reads a scan this degraded.
3. **Speed is not the problem.** All engines are 0.8–4s/page on readable scans on macOS; the default is already
   ~1s (docling auto-selects Apple Vision on macOS). The "~60s" was most likely the whole 5-page doc, a
   first-run model download, or a Linux RapidOCR-CPU path (no Apple Vision) — not per-page here.

## Implications

- **Engine choice is a speed/deployment decision, not an accuracy one.** Readable scans: keep the fastest per
  platform — Apple Vision (macOS, the default), Tesseract (Linux CPU), or a GPU engine on the A100/Modal
  substrate. Granite-Docling is worth it only for **structured** readable docs (tables/forms), never as a
  degraded-scan rescue.
- **The accuracy lever is upstream of OCR:** (a) an image **pre-processing** stage (deskew, denoise,
  binarize/threshold, contrast/CLAHE, optional super-resolution) to push a "heavy" scan toward "moderate" where
  OCR already scores 1.000; and (b) a **scan-quality gate** that flags an unreadable page (low OCR confidence /
  high garbage-ratio) as PARTIAL / needs-rescan rather than silently ingesting gibberish — ties into the
  lossless/visible-loss philosophy (0006-C / ENG-1).
