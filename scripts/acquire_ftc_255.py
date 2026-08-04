"""CC-0 (compliance rung 1, roadmap §13 C-2): acquire the FTC Endorsement Guides rulebook.

Fetches 16 CFR Part 255 (Guides Concerning Use of Endorsements and Testimonials in Advertising) from the
public eCFR API and parses it into a clean, ingestable form. This is the REGULATORY corpus side of the
compliance module -- the requirement_extraction capability (CC-2) reads these sections into Requirement nodes.

Outputs under `data/compliance/ftc_16cfr255/` (small public text, committed for reproducible smokes):
  - 16cfr255.xml            the raw eCFR source (provenance)
  - 16cfr255.sections.json  [{section, heading, text}] -- one entry per § (255.0 .. 255.6)
  - 16cfr255.txt            flat section-delimited plain text (docling/ingest input)

  uv run --no-sync python -m scripts.acquire_ftc_255
"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from pathlib import Path

OUT = Path("data/compliance/ftc_16cfr255")
XML = OUT / "16cfr255.xml"
# a stable recent eCFR issue date; Part 255 is unchanged across 2024-2026 (last amended 2023)
SRC = "https://www.ecfr.gov/api/versioner/v1/full/2026-01-01/title-16.xml?chapter=I&subchapter=B&part=255"


def _strip(fragment: str) -> str:
    """eCFR XML fragment -> clean text: drop tags, unescape entities, collapse whitespace."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_sections(xml: str) -> list[dict]:
    """Split the Part into its § sections (eCFR uses DIV8 per section, HEAD = the heading, P = paragraphs)."""
    sections: list[dict] = []
    for m in re.finditer(r'<DIV8[^>]*N="([^"]*)"[^>]*>(.*?)</DIV8>', xml, re.S):
        number, body = m.group(1), m.group(2)
        head = re.search(r"<HEAD>(.*?)</HEAD>", body, re.S)
        heading = _strip(head.group(1)) if head else number
        paras = [_strip(p.group(1)) for p in re.finditer(r"<P[^>]*>(.*?)</P>", body, re.S)]
        text = "\n".join(p for p in paras if p)
        sections.append({"section": number, "heading": heading, "text": text})
    return sections


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if not XML.exists():  # fetch-if-missing (idempotent; re-runs parse only)
        print(f"[ftc-255] fetching {SRC}", flush=True)
        with urllib.request.urlopen(SRC, timeout=60) as resp:  # noqa: S310 - trusted public gov API
            XML.write_bytes(resp.read())
    xml = XML.read_text(encoding="utf-8")
    sections = parse_sections(xml)
    (OUT / "16cfr255.sections.json").write_text(json.dumps(sections, indent=2), encoding="utf-8")
    flat = "\n\n".join(f"{s['heading']}\n{s['text']}" for s in sections)
    (OUT / "16cfr255.txt").write_text(flat, encoding="utf-8")
    print(f"[ftc-255] {len(sections)} sections, {len(flat)} chars -> {OUT}", flush=True)
    for s in sections:
        print(f"[ftc-255]   {s['section']:8} {s['heading'][:60]} ({len(s['text'])} chars)", flush=True)


if __name__ == "__main__":
    main()
