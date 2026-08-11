"""PROD-2 Phase 1 (ADR-0049 generic-customer lens): acquire ANY eCFR part into the ingestable sections form.

Generalizes `acquire_ftc_255.py` -- the section parser (DIV8=section, HEAD=heading, P=paragraph) is the STANDARD
eCFR-XML structure, so only the part coordinates differ. Fetches the part from the public eCFR API and writes
`<slug>.sections.json` ([{section, heading, text}]) -- the exact shape `RegulationAdapter` ingests -- so any eCFR
regulation flows through the SAME compliance pipeline without new parsing code.

  TITLE=16 PART=233 SUBCHAPTER=B SLUG=ftc_16cfr233 uv run --no-sync python -m scripts.acquire_ecfr

Env: TITLE (CFR title, req), PART (req), CHAPTER (default I), SUBCHAPTER (optional), DATE (eCFR issue date,
default 2026-01-01), SLUG (output dir under data/compliance/, default title{TITLE}cfr{PART}).
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path


def _strip(fragment: str) -> str:
    """eCFR XML fragment -> clean text: drop tags, unescape entities, collapse whitespace."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_sections(xml: str) -> list[dict]:
    """Split a Part into its § sections (eCFR: DIV8 per section, HEAD = heading, P = paragraphs). Generic."""
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
    title = os.environ["TITLE"]
    part = os.environ["PART"]
    chapter = os.environ.get("CHAPTER", "I")
    subchapter = os.environ.get("SUBCHAPTER")
    date = os.environ.get("DATE", "2026-01-01")
    slug = os.environ.get("SLUG", f"title{title}cfr{part}")

    out = Path("data/compliance") / slug
    out.mkdir(parents=True, exist_ok=True)
    xml_path = out / f"{slug}.xml"

    params = {"chapter": chapter, "part": part}
    if subchapter:
        params["subchapter"] = subchapter
    src = f"https://www.ecfr.gov/api/versioner/v1/full/{date}/title-{title}.xml?{urllib.parse.urlencode(params)}"

    if not xml_path.exists():  # fetch-if-missing (idempotent; re-runs parse only)
        print(f"[ecfr] fetching {src}", flush=True)
        with urllib.request.urlopen(src, timeout=60) as resp:  # noqa: S310 - trusted public gov API
            xml_path.write_bytes(resp.read())
    xml = xml_path.read_text(encoding="utf-8")
    sections = parse_sections(xml)
    (out / f"{slug}.sections.json").write_text(json.dumps(sections, indent=2), encoding="utf-8")
    print(f"[ecfr] {len(sections)} sections -> {out}/{slug}.sections.json", flush=True)
    for s in sections:
        print(f"[ecfr]   {s['section']:10} {s['heading'][:60]} ({len(s['text'])} chars)", flush=True)


if __name__ == "__main__":
    main()
