"""CUAD parsing + scanned-PDF detection (T7, docs/Corpus_Acquisition.md).

`party_entities` extracts the real company parties from the CUAD `Parties` annotation, which mixes
company names with defined-term role labels ("Company", "MA", "Marketing Affiliate"). It keeps only
names carrying a corporate suffix and drops bare role labels, conservatively: a role label treated
as an entity would create a *phantom* shared-party link and inflate the multi-hop material with
false connections. Better to miss a party than to invent a shared one.

`is_scanned_pdf` detects an image-only PDF via the system `pdftotext` using a **low character-count
threshold, not strict-zero**, so a stray character on a scanned page does not undercount the scanned
subset (which would starve the vision-to-text path).
"""

from __future__ import annotations

import ast
import csv
import re
import subprocess
import tempfile
from pathlib import Path

from rag_wright.corpus.selection import ContractMeta

RASTER_DPI = 150  # render resolution for the synthetic image-only PDFs (readable for OCR)

# Corporate-form tokens that mark a party string as a real company (not a role label).
_CORP_SUFFIX = re.compile(
    r"\b("
    r"inc|incorporated|corp|corporation|company|co|llc|l\.?l\.?c|llp|lp|ltd|limited|plc|"
    r"gmbh|ag|nv|n\.?v|sa|s\.?a|spa|holdings|group|international|technologies|technology|"
    r"systems|networks|solutions|services|pharmaceuticals|labs|laboratories|ventures|partners|"
    r"associates|enterprises|industries|bank|trust|capital|media|communications"
    r")\b",
    re.IGNORECASE,
)

# Bare defined-term role labels that must never be treated as entities, even when they coincide
# with a corporate-form word ("Company", "Co"). A role label as an entity would invent a shared
# party and inflate the multi-hop material with false links.
_ROLE_LABELS = {
    "company", "co", "the company", "parties", "party", "buyer", "seller", "purchaser", "vendor",
    "supplier", "licensor", "licensee", "customer", "client", "contractor", "agent", "lender",
    "borrower", "guarantor", "distributor", "reseller", "manufacturer", "consultant", "partner",
    "affiliate", "marketing affiliate", "ma",
}

SCANNED_CHAR_THRESHOLD = 300  # first-2-page text below this -> scanned (image-only)


def party_entities(raw: str) -> list[str]:
    """Company parties from a CUAD `Parties` field (a stringified list), role labels dropped."""
    try:
        items = ast.literal_eval(raw) if raw and raw.strip().startswith("[") else []
    except (ValueError, SyntaxError):
        items = []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        name = str(item).strip()
        norm = name.lower().rstrip(".").strip()
        key = name.lower()
        if name and norm not in _ROLE_LABELS and _CORP_SUFFIX.search(name) and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def is_scanned_by_chars(char_count: int, threshold: int = SCANNED_CHAR_THRESHOLD) -> bool:
    """Threshold decision: fewer than `threshold` extractable characters -> scanned."""
    return char_count < threshold


def pdf_text_chars(pdf_path: Path, *, pages: int = 2) -> int:
    """Characters `pdftotext` extracts from the first `pages` pages (0 for a purely scanned PDF)."""
    try:
        result = subprocess.run(
            ["pdftotext", "-l", str(pages), "-q", str(pdf_path), "-"],
            capture_output=True,
            timeout=60,
        )
        return len(result.stdout.decode("utf-8", "ignore").strip())
    except (subprocess.SubprocessError, OSError):
        return 0


def is_scanned_pdf(pdf_path: Path, *, threshold: int = SCANNED_CHAR_THRESHOLD) -> bool:
    """Whether a PDF is scanned/image-only (low extractable-text character count)."""
    return is_scanned_by_chars(pdf_text_chars(pdf_path), threshold)


def rasterize_to_image_pdf(src_pdf: Path, dst_pdf: Path, *, dpi: int = RASTER_DPI) -> int:
    """Render `src_pdf`'s pages to images and write an **image-only** PDF (no text layer) to `dst_pdf`.

    CUAD ships no image-only PDFs (all carry a text layer), so to exercise Docling's OCR /
    vision-to-text path we deterministically re-render a few contracts as scans: poppler `pdftoppm`
    rasterizes each page to PNG, Pillow assembles them into a PDF with no extractable text. Given the
    same source and `dpi` this is reproducible. Returns the page count.
    """
    from PIL import Image  # lazy: only the rasterization path needs Pillow

    dst_pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        prefix = Path(td) / "page"
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), str(src_pdf), str(prefix)],
            check=True,
            capture_output=True,
            timeout=300,
        )
        pages = sorted(Path(td).glob("page*.png"))
        if not pages:
            raise RuntimeError(f"pdftoppm produced no pages for {src_pdf}")
        images = [Image.open(p).convert("RGB") for p in pages]
        images[0].save(dst_pdf, save_all=True, append_images=images[1:])
        return len(images)


def _agreement_type(pdf_path: Path) -> str:
    """The agreement type is the PDF's immediate folder name, underscores folded to spaces."""
    return pdf_path.parent.name.replace("_", " ").strip()


def load_contract_metadata(extracted_root: Path) -> list[ContractMeta]:
    """Build `ContractMeta` for every CUAD contract that has both a CSV row and a PDF.

    Runs `pdftotext` per PDF (scanned detection), so this is the slow, I/O part. `extracted_root` is
    the extracted `CUAD_v1/` directory (holding `master_clauses.csv` and `full_contract_pdf/`).
    """
    pdfs = {
        p.stem.lower(): p
        for p in (extracted_root / "full_contract_pdf").rglob("*")
        if p.suffix.lower() == ".pdf"
    }
    metas: list[ContractMeta] = []
    with open(extracted_root / "master_clauses.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            filename = (row.get("Filename") or "").strip()
            stem = Path(filename).stem.lower()
            pdf = pdfs.get(stem)
            if pdf is None:
                continue
            metas.append(
                ContractMeta(
                    contract_id=Path(filename).stem,
                    agreement_type=_agreement_type(pdf),
                    parties=party_entities(row.get("Parties", "")),
                    is_scanned=is_scanned_pdf(pdf),
                    size_bytes=pdf.stat().st_size,
                )
            )
    return metas
