#!/usr/bin/env python
"""Acquire EDGAR entity data for the CUAD subset's parties (T7).

Fetches the `company_tickers.json` registry seed and, for the subset parties that a conservative
name match proposes a CIK for, the per-company submissions (former names / ticker aliases). Every
fetch goes through the throttled (<=10 req/s), durably-cached, User-Agent'd fetcher, so a re-run is
silent on the network for anything already fetched (protecting EDGAR from re-hits).

Name->CIK matches are mechanical and written UNVERIFIED to a `proposed/` location; human
verification (T10) is the only path to ground truth.

    uv run python scripts/acquire_edgar.py --check   # dry run: coverage, no submissions fetch
    uv run python scripts/acquire_edgar.py           # fetch submissions + write proposals
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

from rag_wright.corpus.edgar import UNRESOLVED_NOTE, propose_matches
from rag_wright.corpus.http import DiskCache, RateLimiter, ThrottledCachingFetcher

ROOT = Path("data")
EDGAR = ROOT / "edgar"
CACHE = EDGAR / "cache"
PROPOSED = EDGAR / "proposed"
CUAD_SUBSET = ROOT / "cuad" / "subset"
META_CACHE = ROOT / "cuad" / "metadata_cache.json"
USER_AGENT = "RAG_Wright research farhan.zaidi@dreamai.io"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_network_calls = {"n": 0}


def transport(url: str, headers: dict[str, str]) -> bytes:
    _network_calls["n"] += 1
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content


def subset_party_names() -> list[str]:
    """The (deduped) company party names of the selected CUAD subset."""
    selected = set(json.loads((CUAD_SUBSET / "manifest.json").read_text())["selected_ids"])
    metas = json.loads(META_CACHE.read_text())
    names: list[str] = []
    for meta in metas:
        if meta["contract_id"] in selected:
            names.extend(meta["parties"])
    return list(dict.fromkeys(names))  # dedupe, preserve order


def main() -> None:
    ap = argparse.ArgumentParser(description="Acquire EDGAR entity data (T7)")
    ap.add_argument("--check", action="store_true", help="dry run: coverage only, no submissions")
    args = ap.parse_args()

    fetcher = ThrottledCachingFetcher(
        user_agent=USER_AGENT,
        cache=DiskCache(CACHE),
        limiter=RateLimiter(max_per_sec=10),
        transport=transport,
    )

    tickers = json.loads(fetcher.get(TICKERS_URL))
    company_tickers = list(tickers.values())
    party_names = subset_party_names()
    coverage = propose_matches(party_names, company_tickers)

    if not args.check:
        for proposal in coverage.resolved:
            try:
                fetcher.get(SUBMISSIONS_URL.format(cik=proposal.proposed_cik))
            except requests.RequestException as exc:
                print(f"  [edgar] submissions fetch failed for {proposal.proposed_cik}: {exc}")
        PROPOSED.mkdir(parents=True, exist_ok=True)
        (PROPOSED / "name_to_cik.json").write_text(
            json.dumps(
                {
                    "note": UNRESOLVED_NOTE,
                    "status": "ALL PROPOSALS UNVERIFIED - human verification at T10 is ground truth",
                    "resolved": [p.model_dump() for p in coverage.resolved],
                    "unresolved": coverage.unresolved,
                },
                indent=2,
            )
        )

    print("\n=== EDGAR acquisition ===")
    print(f"  registry seed   : {len(company_tickers)} public companies (company_tickers.json)")
    print(f"  subset parties  : {len(party_names)}")
    print(f"  resolved (prop) : {len(coverage.resolved)}  (UNVERIFIED)")
    print(f"  unresolved      : {len(coverage.unresolved)}  (expected: private/variant entities)")
    print(f"  network calls   : {_network_calls['n']}")
    if not args.check:
        print(f"  proposals -> {PROPOSED / 'name_to_cik.json'}")


if __name__ == "__main__":
    main()
