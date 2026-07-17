#!/usr/bin/env python
"""Acquire ACORD (T33). Pinned source: HuggingFace theatticusproject/acord (CC BY 4.0).

ACORD (Atticus Clause Retrieval Dataset) is the content-bearing, independently-authored retrieval bar
CUAD cannot supply (ADR-0011): 114 attorney-authored queries with graded query-clause relevance over a
self-contained clause corpus. The whole benchmark is used (no subsetting — unlike the CUAD acquisition),
so this just downloads the one pinned zip, extracts it, records the CC BY attribution, and reports the
extracted layout under gitignored data/.

    uv run python scripts/acquire_acord.py            # download + extract + attribution + report
"""

from __future__ import annotations

import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path("data/acord")
RAW = ROOT / "raw"
EXTRACTED = ROOT / "extracted"
ZIP_URL = (
    "https://huggingface.co/datasets/theatticusproject/acord/resolve/main/"
    "ACORD%20Dataset%20%26%20ReadMe.zip"
)
ZIP_PATH = RAW / "ACORD_Dataset_and_ReadMe.zip"
SOURCE_SNAPSHOT = "huggingface:theatticusproject/acord"
USER_AGENT = "RAG_Wright research farhan.zaidi@dreamai.io"
CC_BY_ATTRIBUTION = (
    "ACORD (Atticus Clause Retrieval Dataset), by The Atticus Project, licensed under the Creative "
    "Commons Attribution 4.0 International License (CC BY 4.0), "
    "https://creativecommons.org/licenses/by/4.0/. Source: HuggingFace dataset "
    "theatticusproject/acord (https://huggingface.co/datasets/theatticusproject/acord). No changes are "
    "asserted to the underlying clauses; this project uses the benchmark for retrieval evaluation."
)


def ensure_downloaded() -> None:
    if ZIP_PATH.exists() and ZIP_PATH.stat().st_size > 0:
        print(f"[acord] already downloaded: {ZIP_PATH} ({ZIP_PATH.stat().st_size} bytes)")
        return
    RAW.mkdir(parents=True, exist_ok=True)
    print(f"[acord] downloading {ZIP_URL} -> {ZIP_PATH} ...")
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp, open(ZIP_PATH, "wb") as out:
        shutil.copyfileobj(resp, out)
    print(f"[acord] downloaded {ZIP_PATH.stat().st_size} bytes")


def ensure_extracted() -> None:
    if EXTRACTED.exists() and any(EXTRACTED.iterdir()):
        print(f"[acord] already extracted under {EXTRACTED}")
        return
    EXTRACTED.mkdir(parents=True, exist_ok=True)
    print(f"[acord] extracting {ZIP_PATH} -> {EXTRACTED} ...")
    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extractall(EXTRACTED)


def write_attribution() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "ATTRIBUTION.txt").write_text(
        f"{CC_BY_ATTRIBUTION}\n\nSource snapshot: {SOURCE_SNAPSHOT}\n", encoding="utf-8"
    )


def report_layout() -> None:
    print(f"\n[acord] extracted layout under {EXTRACTED}:")
    for path in sorted(EXTRACTED.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(EXTRACTED)}  ({path.stat().st_size} bytes)")


def main() -> None:
    ensure_downloaded()
    ensure_extracted()
    write_attribution()
    report_layout()


if __name__ == "__main__":
    main()
