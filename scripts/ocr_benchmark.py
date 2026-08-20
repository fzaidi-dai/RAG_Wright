"""OCR benchmark harness (OCR-EVAL): measure OCR ACCURACY (and speed) across docling's OCR engines and VLM
pipelines, against ground truth. NOT the full ingest pipeline -- this isolates the OCR question.

Two ground-truth conventions, auto-detected per PDF in the corpus dir:
  - `<stem>.ground-truth.txt`  -> whole-document GT (the RuleWright clean/moderate/heavy contract fixtures)
  - `page_<N>.txt`             -> per-page GT (the missouri notice)

Accuracy is a no-dependency similarity vs GT (difflib ratio on whitespace/case-normalized text) at character
and word level (1.0 = identical). Speed is wall-clock seconds/page.

Backends are a registry; each declares an availability probe so an uninstalled engine is SKIPPED (reported),
never a hard error. Add a backend by registering a (builder, availability) pair.

  uv run --no-sync python -m scripts.ocr_benchmark --corpus /Users/farhan/work/RuleWright/docs/fixtures/ocr
  uv run --no-sync python -m scripts.ocr_benchmark --corpus temp/ocr_test --backends tesseract ocrmac
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import time
from pathlib import Path
from statistics import mean

_DEFAULT_CORPUS = Path("/Users/farhan/work/RuleWright/docs/fixtures/ocr")


# --- accuracy (no extra deps) -------------------------------------------------------------------

def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _char_sim(ocr: str, gt: str) -> float:
    return difflib.SequenceMatcher(None, _norm(ocr), _norm(gt)).ratio()


def _word_sim(ocr: str, gt: str) -> float:
    return difflib.SequenceMatcher(None, _norm(ocr).split(), _norm(gt).split()).ratio()


# --- docling converter builders -----------------------------------------------------------------

def _pdf_ocr_converter(ocr_options):
    """Standard PDF pipeline with a chosen OCR engine, forced to full-page OCR (scanned doc)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.ocr_options = ocr_options
    if hasattr(opts.ocr_options, "force_full_page_ocr"):
        opts.ocr_options.force_full_page_ocr = True
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _vlm_converter(vlm_spec):
    """The end-to-end VLM pipeline (image -> transcription): local (Granite/SmolDocling) or a remote API VLM."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import VlmPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline

    opts = VlmPipelineOptions(vlm_options=vlm_spec)
    opts.enable_remote_services = True  # allow API-based VLMs (OpenRouter); harmless for local specs
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_cls=VlmPipeline, pipeline_options=opts)})


def _openrouter_vlm(model_id: str):
    """A remote VLM via OpenRouter (OpenAI-compatible) -- the same provider we already use for Gemma-4. Reads a
    document page image and transcribes its text; language context lets it read blurred/degraded scans that
    character-based OCR cannot. `scale=3.0` renders the page at higher resolution for the model."""
    import os

    from docling.datamodel.pipeline_options import ApiVlmOptions, ResponseFormat

    return ApiVlmOptions(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        params={"model": model_id, "max_tokens": 8192},
        prompt=("Transcribe ALL text from this document page exactly as it appears, preserving reading order. "
                "Output only the transcribed text (markdown), no commentary."),
        response_format=ResponseFormat.MARKDOWN, scale=3.0, timeout=180.0)


def _openrouter_avail():
    import os

    return (bool(os.environ.get("OPENROUTER_API_KEY")), "OPENROUTER_API_KEY not set")


def _spec(name):
    from docling.datamodel import vlm_model_specs

    return getattr(vlm_model_specs, name)


def _ocr_opts(cls_name):
    mod = __import__("docling.datamodel.pipeline_options", fromlist=[cls_name])
    return getattr(mod, cls_name)()


def _importable(*mods) -> tuple[bool, str]:
    import importlib.util as u

    missing = [m for m in mods if u.find_spec(m) is None]
    return (not missing, "" if not missing else f"missing: {', '.join(missing)}")


def _tesseract_avail() -> tuple[bool, str]:
    import shutil

    return (shutil.which("tesseract") is not None, "tesseract CLI not on PATH")


# name -> (builder: () -> converter, availability: () -> (ok, reason))
BACKENDS: dict[str, tuple] = {
    # the ACTUAL ingest path today: a bare DocumentConverter() (docling defaults) -- what our DoclingParser uses.
    "docling_default": (
        lambda: __import__("docling.document_converter", fromlist=["DocumentConverter"]).DocumentConverter(),
        lambda: (True, "")),
    "rapidocr": (lambda: _pdf_ocr_converter(_ocr_opts("RapidOcrOptions")),
                 lambda: _importable("rapidocr") if _importable("rapidocr")[0] else _importable("onnxruntime")),
    "tesseract": (lambda: _pdf_ocr_converter(_ocr_opts("TesseractCliOcrOptions")), _tesseract_avail),
    "ocrmac": (lambda: _pdf_ocr_converter(_ocr_opts("OcrMacOptions")), lambda: _importable("ocrmac")),
    "easyocr": (lambda: _pdf_ocr_converter(_ocr_opts("EasyOcrOptions")), lambda: _importable("easyocr")),
    "granite_docling_mlx": (lambda: _vlm_converter(_spec("GRANITEDOCLING_MLX")), lambda: _importable("mlx_vlm")),
    "granite_docling_transformers": (
        lambda: _vlm_converter(_spec("GRANITEDOCLING_TRANSFORMERS")), lambda: _importable("transformers")),
    "smoldocling_mlx": (lambda: _vlm_converter(_spec("SMOLDOCLING_MLX")), lambda: _importable("mlx_vlm")),
    # remote VLMs via OpenRouter (no local model, no Modal) -- language-context OCR for degraded scans
    "openrouter_gemma4": (lambda: _vlm_converter(_openrouter_vlm("google/gemma-4-31b-it")), _openrouter_avail),
    "openrouter_qwen25vl": (
        lambda: _vlm_converter(_openrouter_vlm("qwen/qwen-2.5-vl-72b-instruct")), _openrouter_avail),
}


# --- OCR text extraction ------------------------------------------------------------------------

def _whole_text(doc) -> str:
    parts = [(getattr(item, "text", "") or "").strip() for item, _ in doc.iterate_items()]
    return "\n".join(p for p in parts if p)


def _per_page_text(doc) -> dict[int, str]:
    pages: dict[int, list[str]] = {}
    for item, _level in doc.iterate_items():
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        prov = getattr(item, "prov", None) or []
        pg = prov[0].page_no if prov else 1
        pages.setdefault(pg, []).append(text)
    return {pg: "\n".join(v) for pg, v in pages.items()}


def _n_pages(pdf: Path) -> int:
    import pypdfium2 as pdfium

    return len(pdfium.PdfDocument(str(pdf)))


# --- ground truth: whole-doc (<stem>.ground-truth.txt) or per-page (page_N.txt) -----------------

def _find_gt(corpus: Path, pdf: Path):
    whole = corpus / f"{pdf.stem}.ground-truth.txt"
    if whole.exists():
        return ("whole", whole.read_text(encoding="utf-8", errors="ignore"))
    pages: dict[int, str] = {}
    for f in sorted(corpus.glob("page_*.txt")):
        m = re.search(r"page[_-]?(\d+)", f.name, re.I)
        if m:
            pages[int(m.group(1))] = f.read_text(encoding="utf-8", errors="ignore")
    return ("per_page", pages) if pages else (None, None)


def run_backend(name: str, pdf: Path, gt_kind: str, gt, n_pages: int) -> dict:
    builder, avail = BACKENDS[name]
    ok, why = avail()
    if not ok:
        return {"backend": name, "pdf": pdf.name, "available": False, "reason": why}
    try:
        conv = builder()
        t0 = time.perf_counter()
        doc = conv.convert(str(pdf)).document
        elapsed = time.perf_counter() - t0
        if gt_kind == "whole":
            char_sim, word_sim = _char_sim(_whole_text(doc), gt), _word_sim(_whole_text(doc), gt)
        else:
            pages = _per_page_text(doc)
            char_sim = mean(_char_sim(pages.get(pg, ""), gt[pg]) for pg in gt)
            word_sim = mean(_word_sim(pages.get(pg, ""), gt[pg]) for pg in gt)
        return {"backend": name, "pdf": pdf.name, "available": True,
                "seconds_total": round(elapsed, 2), "seconds_per_page": round(elapsed / max(n_pages, 1), 2),
                "char_sim": round(char_sim, 4), "word_sim": round(word_sim, 4)}
    except Exception as exc:  # noqa: BLE001 - a backend that errors is reported, never sinks the whole run
        return {"backend": name, "pdf": pdf.name, "available": True, "error": f"{type(exc).__name__}: {exc}"[:300]}


def main() -> None:
    ap = argparse.ArgumentParser(description="OCR accuracy/speed benchmark over docling engines")
    ap.add_argument("--corpus", type=Path, default=_DEFAULT_CORPUS)
    ap.add_argument("--backends", nargs="*", default=list(BACKENDS))
    ap.add_argument("--preprocess", choices=["none", "basic", "sauvola", "enhance"], default="none",
                    help="0009: classical image preprocessing before OCR (render->denoise->deskew->binarize)")
    ap.add_argument("--dpi", type=int, default=200, help="render DPI when preprocessing")
    ap.add_argument("--out", type=Path, default=Path("temp/ocr_test/results.json"))
    args = ap.parse_args()

    pdfs = sorted(args.corpus.glob("*.pdf"))
    print(f"[ocr-bench] corpus={args.corpus} pdfs={[p.name for p in pdfs]} backends={args.backends} "
          f"preprocess={args.preprocess}\n", flush=True)

    import tempfile
    prep_dir = Path(tempfile.mkdtemp(prefix="ocr_prep_")) if args.preprocess != "none" else None

    rows = []
    for pdf in pdfs:
        gt_kind, gt = _find_gt(args.corpus, pdf)
        if gt_kind is None:
            print(f"[ocr-bench] no ground truth for {pdf.name}, skipping", flush=True)
            continue
        n = _n_pages(pdf)
        run_pdf = pdf
        if args.preprocess != "none":  # build the cleaned PDF ONCE, then run every backend on it
            from scripts.ocr_preprocess import preprocess_pdf

            run_pdf = preprocess_pdf(pdf, prep_dir / f"{pdf.stem}.{args.preprocess}.pdf", args.preprocess, args.dpi)
        print(f"[ocr-bench] === {pdf.name} ({n} pages, {gt_kind} GT, preprocess={args.preprocess}) ===", flush=True)
        for name in args.backends:
            if name not in BACKENDS:
                continue
            r = run_backend(name, run_pdf, gt_kind, gt, n)
            r["pdf"] = pdf.name  # report under the ORIGINAL name, even when run on the cleaned PDF
            rows.append(r)
            if not r.get("available"):
                print(f"    {name:30} SKIP ({r['reason']})", flush=True)
            elif r.get("error"):
                print(f"    {name:30} ERROR {r['error']}", flush=True)
            else:
                print(f"    {name:30} {r['seconds_per_page']:>6.2f}s/pg  char_sim={r['char_sim']:.3f}  "
                      f"word_sim={r['word_sim']:.3f}", flush=True)

    # accuracy matrix: backend x pdf -> char_sim
    ok = [r for r in rows if r.get("available") and not r.get("error")]
    pdf_names = [p.name for p in pdfs]
    print("\n[ocr-bench] ACCURACY MATRIX (char_sim; higher = better):")
    print(f"  {'backend':30} " + " ".join(f"{n[:16]:>17}" for n in pdf_names))
    for name in args.backends:
        cells = {r["pdf"]: r for r in ok if r["backend"] == name}
        if not cells:
            continue
        line = f"  {name:30} "
        for pn in pdf_names:
            c = cells.get(pn)
            line += f"{(f'{c['char_sim']:.3f}@{c['seconds_per_page']:.1f}s' if c else '--'):>17} "
        print(line, flush=True)

    args.out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n[ocr-bench] wrote {args.out}")


if __name__ == "__main__":
    main()
