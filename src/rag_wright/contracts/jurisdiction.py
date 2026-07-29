"""KG-5a: deterministic jurisdiction canonicalization for the contract KG.

The typed KG stores `jurisdiction` as the extracted surface string (`England`, `England and Wales`,
`English law`, `State of New York`, `State of New York, USA`, ...). Retrieval matching (Leg B) fails on
those variants because the query side and the clause side don't share a normalized value. This maps a
surface form to a **canonical jurisdiction slug** (or None for non-jurisdictions), deterministically -- no
LLM, no network. Applied additively: the value node keeps its surface `value` and gains a `canonical_value`;
the query constraint is canonicalized the same way, so both meet on the canonical.

Method: strip governance boilerplate prefixes/suffixes (`State of`, `Commonwealth of`, `, USA`, ` law`,
` courts`) and normalize, then look up a gazetteer (50 US states + the countries seen in the corpus, each
with aliases). Non-jurisdictions (`Applicable Law`, `Not specified`, `worldwide`, redactions, compound
`Illinois or New York`) resolve to None (left as their surface value, unmatched).
"""

from __future__ import annotations

import re

# canonical slug -> alias surface forms (already normalized: lowercase, no prefixes/suffixes)
_US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
]

# country / non-US aliases -> canonical slug (normalized)
_COUNTRY_ALIASES: dict[str, str] = {
    "england": "england", "english": "england", "england and wales": "england",
    "united kingdom": "england", "uk": "england", "great britain": "england", "britain": "england",
    "scotland": "scotland", "wales": "wales", "northern ireland": "northern_ireland",
    "china": "china", "prc": "china", "people's republic of china": "china",
    "united states": "united_states", "united states of america": "united_states", "usa": "united_states",
    "u.s.a.": "united_states", "us": "united_states",
    "canada": "canada", "british columbia": "british_columbia", "ontario": "ontario",
    "belgium": "belgium", "germany": "germany", "france": "france", "japan": "japan", "spain": "spain",
    "italy": "italy", "italian": "italy", "south africa": "south_africa", "israel": "israel",
    "taiwan": "taiwan", "netherlands": "netherlands", "switzerland": "switzerland", "australia": "australia",
    "singapore": "singapore", "hong kong": "hong_kong", "ireland": "ireland",
}

# build the normalized-alias -> canonical index
_INDEX: dict[str, str] = {s: s.replace(" ", "_") for s in _US_STATES}
_INDEX.update(_COUNTRY_ALIASES)

# surfaces that are NOT a jurisdiction (governance boilerplate / nulls / references / vague) -> None
_JUNK = re.compile(
    r"applicable\s+law|governing\s+law|not\s+specified|not\s+explicitly|unspecified|none\s+specified"
    r"|^none$|^other$|best's|exhibit|section\b|bankruptcy\s+code|any\s+jurisdiction|jurisdiction\s+governing"
    r"|franchised\s+restaurant|^territory$|^union$|^worldwide$|\*|\[|last\s+sentence|internal\s+laws",
    re.IGNORECASE,
)
_PREFIXES = ("the state of ", "state of ", "the commonwealth of ", "commonwealth of ",
             "the province of ", "province of ", "the ")
_SUFFIXES = (", u.s.a.", ", usa", ", united states of america", ", united states", " (u.s.a.)",
             " (usa)", " and its territories")


def _normalize(surface: str) -> str:
    s = " ".join(surface.lower().strip().split())
    for p in _PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
            break
    for suf in _SUFFIXES:
        s = s.replace(suf, "")
    s = re.sub(r"\s+(law|laws|courts|court|state)$", "", s).strip()
    return s


# longest alias first, for the containment fallback (word-bounded)
_ALIAS_ITEMS = sorted(_INDEX.items(), key=lambda kv: -len(kv[0]))


def _containment(norm: str) -> str | None:
    """Fallback for surfaces the exact lookup misses: scan for known jurisdiction aliases as whole words.
    Resolve only if it names exactly one place -- treating a US state named alongside 'the United States'
    (federal) as that state ('New York and ... the United States of America' -> new_york). Genuinely
    ambiguous compounds ('Illinois or New York') stay None."""
    found = {canon for alias, canon in _ALIAS_ITEMS if re.search(rf"\b{re.escape(alias)}\b", norm)}
    if len(found) > 1:
        found.discard("united_states")  # a state + US federal -> the state
    return next(iter(found)) if len(found) == 1 else None


def canonicalize_jurisdiction(surface: str) -> str | None:
    """Map a jurisdiction surface form to a canonical slug (e.g. 'england', 'new_york'), or None if it is
    not a resolvable single jurisdiction (boilerplate, null, reference, ambiguous compound). Deterministic."""
    if not surface or _JUNK.search(surface):
        return None
    norm = _normalize(surface)
    return _INDEX.get(norm) or _containment(norm)
