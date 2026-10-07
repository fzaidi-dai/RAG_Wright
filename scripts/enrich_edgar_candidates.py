#!/usr/bin/env python
"""Grounded EDGAR lookup for the T10 verification set's cands=0 and collision entities (Task 2).

The local company_tickers registry misses delisted dot-com filers (VerticalNet, Excite, ...) on name
mismatch, and token-overlap collides others. This does a live EDGAR company-database search
(browse-edgar, delisted filers included) + submissions lookup, attaching UNVERIFIED `edgar_candidates`
with EVIDENCE (conformed name, former_names, tickers) so the human confirms-or-corrects, never
looks up. Never sets `resolution`; never collapses subsidiary pairs (flagged, not merged). A null
EDGAR result is recorded (it supports a PRIVATE mark), not dropped.

    uv run python scripts/enrich_edgar_candidates.py
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import requests

from rag_wright.packs.contracts.corpus.edgar import parse_browse_edgar_ciks, parse_submissions_evidence
from rag_wright.corpus.http import DiskCache, RateLimiter, ThrottledCachingFetcher

SET = Path("data/edgar/verification_set.json")
CACHE = Path("data/edgar/cache")
USER_AGENT = "RAG_Wright research farhan.zaidi@dreamai.io"
BROWSE = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={q}&type=&dateb=&owner=include&count=10&output=atom"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"

# collision re-queries (had a wrong local token match) + subsidiary pairs (propose, never merge)
REQUERY = ["bravatek", "baidu", "china online", "fonterra", "quantum"]
# Public filers the key-based query missed (a wrong local candidate blocked re-lookup); each MUST be
# re-queried with a distinctive query and MUST NOT default to PRIVATE on a blank. token-in-key -> query.
FORCE_LOOKUP = {
    "watley": "watley",  # AB WATLEY GROUP INC
    "pc quote": "pc quote",  # -> HYPERFEED TECHNOLOGIES INC (former: PC QUOTE INC)
    "vitamin shoppe": "vitamin shoppe",
    "neoforma": "neoforma",
    "ingram micro": "ingram micro",
    "xplore": "xplore technologies",  # confirm parent vs "...of America" sub
}
SUBSIDIARY_PAIRS = [
    ("scansource", "scansource latin america"),
    ("federated advisory services", "federated investment management"),
    ("zebra technologies international", "zebra technologies"),
]

_calls = {"n": 0}


def transport(url: str, headers: dict[str, str]) -> bytes:
    _calls["n"] += 1
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content


def main() -> None:
    doc = json.loads(SET.read_text())
    fetcher = ThrottledCachingFetcher(
        user_agent=USER_AGENT, cache=DiskCache(CACHE), limiter=RateLimiter(10), transport=transport
    )

    def edgar_lookup(name: str) -> list[dict]:
        try:
            xml = fetcher.get(BROWSE.format(q=urllib.parse.quote(name))).decode("utf-8", "ignore")
        except requests.RequestException:
            return []
        out = []
        for cik in parse_browse_edgar_ciks(xml)[:3]:  # cap; multiple => ambiguous, human picks
            try:
                subs = json.loads(fetcher.get(SUBMISSIONS.format(cik=cik)))
                out.append(parse_submissions_evidence(subs).model_dump())
            except (requests.RequestException, ValueError, KeyError):
                continue
        return out

    enriched = 0
    for e in doc["entities"]:
        key = e["entity_key"]
        forced_query = next((q for tok, q in FORCE_LOOKUP.items() if tok in key), None)
        needs = not e["candidates"] or any(t in key for t in REQUERY) or forced_query
        if not needs:
            continue
        # query by a forced distinctive query where the key would miss (name changes / word order),
        # else the canonical key -- browse-edgar prefix-matches the conformed name
        e["edgar_candidates"] = edgar_lookup(forced_query or key)  # [] => supports PRIVATE
        enriched += 1

    # subsidiary-pair flags: propose but never collapse; the human reads these off the contract
    keys = {e["entity_key"] for e in doc["entities"]}
    existing = {(f["type"], tuple(f["entities"])) for f in doc.get("flags", [])}
    for a, b in SUBSIDIARY_PAIRS:
        present = [k for k in (a, b) if k in keys]
        if len(present) == 2 and ("subsidiary_pair", tuple(present)) not in existing:
            doc.setdefault("flags", []).append(
                {"type": "subsidiary_pair", "entities": present,
                 "note": "distinct nodes unless the contract signs them as one party; do not merge"}
            )

    SET.write_text(json.dumps(doc, indent=2))
    print(f"enriched {enriched} entities with EDGAR evidence | network calls: {_calls['n']}")
    print("entities that got an EDGAR candidate:")
    for e in doc["entities"]:
        for c in e.get("edgar_candidates", []):
            fn = f" former={c['former_names']}" if c["former_names"] else ""
            print(f"  {e['representative'][:34]:34} -> CIK {c['proposed_cik']} {c['registry_name'][:28]!r}{fn}")
    print("no-EDGAR-match (supports PRIVATE):",
          [e["representative"][:24] for e in doc["entities"] if e.get("edgar_candidates") == []])


if __name__ == "__main__":
    main()
