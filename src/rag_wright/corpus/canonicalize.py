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
from typing import Optional

from pydantic import BaseModel

# Company-form suffixes stripped for the clustering key, so "Bank of America" and "Bank of America,
# N.A." collapse to one key. Applied repeatedly (a name may carry several, e.g. "Co., Ltd.").
_LEGAL_SUFFIX = re.compile(
    r"[,\.\s]+\b("
    r"incorporated|inc|corporation|corp|company|co|llc|l\.?l\.?c|llp|l\.?p|lp|ltd|limited|plc|"
    r"gmbh|ag|n\.?\s*v\.?|nv|s\.?\s*a\.?|sa|s\.?p\.?a|spa|n\.?\s*a\.?|na|pllc"
    r")\b\.?$",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_APOSTROPHE = re.compile(r"['’‘`]")  # so "Stremick's" == "Stremicks"
_PLACEHOLDER = re.compile(r"<<|>>|_{2,}|\bxxx+\b|\benter\b|company name|\[\s*\]", re.IGNORECASE)
# Alias markers: everything from the marker on is an alias, not part of the registered name, so
# "Acme Inc. d/b/a Brand" -> "Acme Inc." and a mention that is only an alias phrase ("formerly known
# as Tradeum, Inc. which d/b/a VerticalNet Solutions") strips to empty and is rejected.
_ALIAS_MARKER = re.compile(
    r"\b(formerly known as|doing business as|also known as|"
    r"f\s*/?\s*k\s*/?\s*a|d\s*/?\s*b\s*/?\s*a|a\s*/?\s*k\s*/?\s*a)\b",
    re.IGNORECASE,
)
# Bare over-broad words that are not entities on their own in any domain (a key equal to one of these, after suffix
# stripping, is an over-broad match, not an organisation). A domain's ROLE words and phrases (e.g. a contract's
# "the applicant", "the supplier") are the domain's: it passes them as `EntityRules` (PS-R5b, ADR-0066).
_OVERBROAD = frozenset(
    {
        "bank", "company", "co", "corporation", "corp", "trust", "group", "holdings", "holding",
        # bare generic-token fragments that are not entities on their own
        "services", "solutions", "systems", "technologies", "technology", "international",
        "enterprises", "industries", "communications", "networks", "media", "capital",
        "management", "ventures", "partners", "associates", "advisors", "advisory", "consulting",
        "worldwide", "global", "products",
    }
)


def _strip_alias_clause(name: str) -> str:
    """Cut an alias phrase ('... d/b/a X', '... formerly known as Y'), keeping the name before it."""
    match = _ALIAS_MARKER.search(name)
    return name[: match.start()].strip(" ,;(") if match else name


def normalize_entity_name(name: str) -> str:
    """Clustering key: lowercase, surrounding quotes/parens and company-form suffixes stripped, whitespace
    collapsed. Merges `Bank of America`, `Bank of America, N.A.`, `Bank of America, N. A`."""
    text = _strip_alias_clause(name).strip().strip("\"'()[]").lower()
    text = _APOSTROPHE.sub("", text)  # possessive: "stremick's" -> "stremicks"
    prev = None
    while prev != text:  # strip stacked suffixes ("co., ltd.")
        prev = text
        text = _LEGAL_SUFFIX.sub("", text).strip(" ,.")
    return _NON_ALNUM.sub(" ", text).strip()


class EntityRules(BaseModel):
    """PS-R5b: a domain's own non-entity vocabulary, declared in its pack (ADR-0066) and passed to `is_entity` /
    `disambiguate` / `resolve_entities`. `role_terms`: names that are a role, not an entity, once normalized (a
    role such as "the applicant" or "the supplier"); `role_phrases`: phrases that mark a mention as a role description
    rather than a name (e.g. "together with", "collectively")."""

    model_config = {"frozen": True}

    role_terms: frozenset[str] = frozenset()
    role_phrases: tuple[str, ...] = ()


def is_entity(name: str, rules: Optional[EntityRules] = None) -> bool:
    """Reject non-entities: template placeholders and bare over-broad tokens, plus the domain's role words and
    phrases when `rules` are given."""
    stripped = name.strip().strip("\"'()[]")
    if not stripped or _PLACEHOLDER.search(name):
        return False
    if rules is not None and rules.role_phrases and re.search(
            r"\b(" + "|".join(re.escape(p) for p in rules.role_phrases) + r")\b", name, re.IGNORECASE):
        return False
    key = normalize_entity_name(name)
    return bool(key) and key not in _OVERBROAD and (rules is None or key not in rules.role_terms)


class EntityCluster(BaseModel):
    """One real-world entity: its clustering key, a representative surface form, and all variants."""

    key: str
    representative: str  # a full surface form (the longest), for display + EDGAR matching
    variants: list[str]


def cluster_entities(names: list[str], rules: Optional[EntityRules] = None) -> list[EntityCluster]:
    """Cluster surface forms into entities by normalized key (non-entities rejected first, with the domain's
    `rules` when given)."""
    groups: dict[str, list[str]] = {}
    for name in names:
        if not is_entity(name, rules):
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
