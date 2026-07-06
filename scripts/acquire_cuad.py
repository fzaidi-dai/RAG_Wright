#!/usr/bin/env python
"""Acquire and subset CUAD (T7). Pinned source: Zenodo record 4595826 (CUAD_v1.zip, CC BY 4.0).

Downloads the pinned CUAD snapshot once (cached), extracts it, builds per-contract metadata (with
scanned detection cached so re-runs are fast), runs the coverage-driven selector, and writes the
subset PDFs + a reproducible manifest + the CC BY attribution under gitignored data/.

    uv run python scripts/acquire_cuad.py --check    # dry run: print coverage, write nothing
    uv run python scripts/acquire_cuad.py            # write the subset + manifest + license
"""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

from rag_wright.corpus.canonicalize import is_entity, normalize_entity_name
from rag_wright.corpus.cuad import (
    RASTER_DPI,
    load_contract_metadata,
    rasterize_to_image_pdf,
)
from rag_wright.corpus.selection import (
    ContractMeta,
    SelectionCriteria,
    SubsetManifest,
    select_subset,
)

ROOT = Path("data/cuad")
RAW = ROOT / "raw"
EXTRACTED = ROOT / "extracted" / "CUAD_v1"
SUBSET = ROOT / "subset"
META_CACHE = ROOT / "metadata_cache.json"
ZIP_URL = "https://zenodo.org/records/4595826/files/CUAD_v1.zip"
ZIP_PATH = RAW / "CUAD_v1.zip"
SOURCE_SNAPSHOT = "zenodo:4595826"
USER_AGENT = "RAG_Wright research farhan.zaidi@dreamai.io"
CC_BY_ATTRIBUTION = (
    "Contract Understanding Atticus Dataset (CUAD) v1, by The Atticus Project, licensed under the "
    "Creative Commons Attribution 4.0 International License (CC BY 4.0), "
    "https://creativecommons.org/licenses/by/4.0/. Source: Zenodo record 4595826 "
    "(https://zenodo.org/records/4595826); https://www.atticusprojectai.org/cuad. No changes are "
    "asserted to the underlying contracts; this project selects a subset for evaluation."
)


def ensure_downloaded() -> None:
    if ZIP_PATH.exists() and ZIP_PATH.stat().st_size > 0:
        return
    RAW.mkdir(parents=True, exist_ok=True)
    print(f"[cuad] downloading {ZIP_URL} -> {ZIP_PATH} ...")
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp, open(ZIP_PATH, "wb") as out:
        shutil.copyfileobj(resp, out)


def ensure_extracted() -> None:
    if (EXTRACTED / "master_clauses.csv").exists():
        return
    print(f"[cuad] extracting {ZIP_PATH} -> {EXTRACTED.parent} ...")
    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extractall(EXTRACTED.parent)


def load_or_build_metadata(refresh: bool) -> list[ContractMeta]:
    if META_CACHE.exists() and not refresh:
        return [ContractMeta(**m) for m in json.loads(META_CACHE.read_text())]
    print("[cuad] building per-contract metadata (pdftotext scanned detection, ~1 min) ...")
    metas = load_contract_metadata(EXTRACTED)
    META_CACHE.write_text(json.dumps([m.model_dump() for m in metas], indent=2))
    return metas


def choose_synthetic_scanned(manifest: SubsetManifest, metas: list[ContractMeta], n: int) -> list[str]:
    """The N smallest selected contracts, re-rendered as image-only PDFs (CUAD has no true scans).

    Smallest-first keeps rasterization cheap and the pages complete; deterministic by (size, id).
    """
    by_id = {c.contract_id: c for c in metas}
    selected = [by_id[i] for i in manifest.selected_ids]
    ordered = sorted(selected, key=lambda c: (c.size_bytes, c.contract_id))
    return [c.contract_id for c in ordered[:n]]


def print_summary(manifest: SubsetManifest, metas: list[ContractMeta], synthetic: list[str]) -> None:
    n_shared = sum(len(g) for g in manifest.shared_party_groups)
    print("\n=== CUAD subset selection ===")
    print(f"  source snapshot   : {manifest.source_snapshot}")
    print(f"  pool contracts    : {len(metas)}  (image-only in CUAD: {sum(c.is_scanned for c in metas)})")
    print(f"  selected          : {len(manifest.selected_ids)}")
    print(f"  synthetic scanned : {len(synthetic)}  (rasterized to image-only @ {RASTER_DPI} dpi)")
    print(f"  agreement types   : {len(manifest.agreement_type_counts)}")
    print(f"  multi-party       : {manifest.multi_party_count}")
    print(f"  shared-party      : {len(manifest.shared_party_groups)} groups, {n_shared} contracts")
    print(f"  total size        : {manifest.total_size_bytes / 1e6:.1f} MB")
    print("  license           : CC BY 4.0 (attribution recorded)")


def write_subset(manifest: SubsetManifest, metas: list[ContractMeta], synthetic: list[str]) -> None:
    pdf_by_stem = {
        p.stem.lower(): p
        for p in (EXTRACTED / "full_contract_pdf").rglob("*")
        if p.suffix.lower() == ".pdf"
    }
    (SUBSET / "pdf").mkdir(parents=True, exist_ok=True)
    synthetic_set = set(synthetic)
    for cid in manifest.selected_ids:
        src = pdf_by_stem.get(cid.lower())
        if src is None:
            continue
        dst = SUBSET / "pdf" / src.name
        if cid in synthetic_set:
            rasterize_to_image_pdf(src, dst)  # image-only render -> exercises OCR / vision-to-text
        else:
            shutil.copy2(src, dst)
    (SUBSET / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    (SUBSET / "LICENSE.txt").write_text(CC_BY_ATTRIBUTION + "\n")
    # The synthetic image-only set + provenance, so the vision-to-text subset is explicit and
    # reproducible, and no one mistakes these for CUAD originals.
    (SUBSET / "scanned.json").write_text(
        json.dumps(
            {
                "note": (
                    "CUAD ships no image-only PDFs (all carry a text layer). These contracts were "
                    "deterministically re-rendered as image-only PDFs (poppler pdftoppm + Pillow) to "
                    "exercise Docling OCR / vision-to-text. Not CUAD originals."
                ),
                "dpi": RASTER_DPI,
                "source_snapshot": manifest.source_snapshot,
                "contract_ids": synthetic,
            },
            indent=2,
        )
    )
    print(f"\n[cuad] wrote {len(manifest.selected_ids)} PDFs "
          f"({len(synthetic)} image-only) + manifest + license to {SUBSET}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Acquire + subset CUAD (T7)")
    ap.add_argument("--check", action="store_true", help="dry run: print coverage, write nothing")
    ap.add_argument("--refresh-metadata", action="store_true", help="rebuild the scanned/metadata cache")
    ap.add_argument("--target-max", type=int, default=150)
    ap.add_argument("--num-scanned", type=int, default=10, help="contracts to rasterize image-only")
    ap.add_argument("--min-shared-party", type=int, default=90, help="relational-density bias (Option C)")
    ap.add_argument("--min-multi-party", type=int, default=30)
    args = ap.parse_args()

    ensure_downloaded()
    ensure_extracted()
    metas = load_or_build_metadata(args.refresh_metadata)
    # T23b method (used here under human verification, Option C): drop noise mentions and cluster
    # legal-suffix/whitespace variants to canonical keys, so shared-party density reflects real
    # entities (a merged Bank of America is one party), not fragmented surface forms.
    clean_metas = [
        m.model_copy(update={"parties": [p for p in m.parties if is_entity(p)]}) for m in metas
    ]
    canon_metas = [
        m.model_copy(update={"parties": sorted({normalize_entity_name(p) for p in m.parties})})
        for m in clean_metas
    ]
    criteria = SelectionCriteria(
        target_max=args.target_max,
        min_scanned=0,  # CUAD ships no image-only PDFs; scanned coverage is synthesized post-select
        min_shared_party_contracts=args.min_shared_party,
        min_multi_party=args.min_multi_party,
    )
    manifest = select_subset(canon_metas, criteria, source_snapshot=SOURCE_SNAPSHOT)
    synthetic = choose_synthetic_scanned(manifest, clean_metas, args.num_scanned)
    print_summary(manifest, clean_metas, synthetic)
    if args.check:
        print("\n[cuad] --check: no files written.")
    else:
        write_subset(manifest, clean_metas, synthetic)


if __name__ == "__main__":
    main()
