"""Entity mention canonicalization -- the normalize / reject / cluster method (T23b).

This is the stage the fragmentation exposed as missing, between recognition (T23) and closed-world
linking (T24): normalize the surface-form variants of one real-world entity to a shared key, reject
non-entities (template placeholders, role artifacts, bare over-broad tokens), and cluster the
variants so one entity is one graph node. It is what stops the graph fragmenting (the
`Bank of America` / `Bank of America, N.A.` / `"<<enter Company Name>>"` problem) and keeps human
name->CIK verification scaling with entity count, not mention count.

Used first here, under human verification, to canonicalize the T10 relational eval set. These
normalize/reject/cluster rules harden into the T23b capability -- keep them.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

# Legal-form suffixes stripped for the clustering key, so "Bank of America" and "Bank of America,
# N.A." collapse to one key. Applied repeatedly (a name may carry several, e.g. "Co., Ltd.").
_LEGAL_SUFFIX = re.compile(
    r"[,\.\s]+\b("
    r"incorporated|inc|corporation|corp|company|co|llc|l\.?l\.?c|llp|l\.?p|lp|ltd|limited|plc|"
    r"gmbh|ag|n\.?\s*v\.?|nv|s\.?\s*a\.?|sa|s\.?p\.?a|spa|n\.?\s*a\.?|na|pllc"
    r")\b\.?$",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_PLACEHOLDER = re.compile(r"<<|>>|_{2,}|\bxxx+\b|\benter\b|company name|\[\s*\]", re.IGNORECASE)

# Bare over-broad / role words that are not entities on their own (a key equal to one of these,
# after suffix stripping, is a role label or an over-broad match, not a company).
_OVERBROAD = frozenset(
    {
        "bank", "company", "co", "parties", "party", "buyer", "seller", "purchaser", "vendor",
        "supplier", "licensor", "licensee", "customer", "client", "contractor", "agent", "lender",
        "borrower", "guarantor", "corporation", "corp", "trust", "group", "holdings", "holding",
        "affiliate", "affiliates", "subsidiary", "the company", "the parties",
    }
)


def normalize_entity_name(name: str) -> str:
    """Clustering key: lowercase, surrounding quotes/parens and legal suffixes stripped, whitespace
    collapsed. Merges `Bank of America`, `Bank of America, N.A.`, `Bank of America, N. A`."""
    text = name.strip().strip("\"'()[]").lower()
    prev = None
    while prev != text:  # strip stacked suffixes ("co., ltd.")
        prev = text
        text = _LEGAL_SUFFIX.sub("", text).strip(" ,.")
    return _NON_ALNUM.sub(" ", text).strip()


def is_entity(name: str) -> bool:
    """Reject non-entities: template placeholders, role artifacts, and bare over-broad tokens."""
    stripped = name.strip().strip("\"'()[]")
    if not stripped or _PLACEHOLDER.search(name):
        return False
    if stripped.lower().startswith("collectively"):
        return False
    key = normalize_entity_name(name)
    return bool(key) and key not in _OVERBROAD


class EntityCluster(BaseModel):
    """One real-world entity: its clustering key, a representative surface form, and all variants."""

    key: str
    representative: str  # a full surface form (the longest), for display + EDGAR matching
    variants: list[str]


def cluster_entities(names: list[str]) -> list[EntityCluster]:
    """Cluster surface forms into entities by normalized key (non-entities rejected first)."""
    groups: dict[str, list[str]] = {}
    for name in names:
        if not is_entity(name):
            continue
        key = normalize_entity_name(name)
        groups.setdefault(key, [])
        if name not in groups[key]:
            groups[key].append(name)
    clusters = [
        EntityCluster(key=key, representative=max(variants, key=len), variants=sorted(variants))
        for key, variants in groups.items()
    ]
    return sorted(clusters, key=lambda c: c.key)
