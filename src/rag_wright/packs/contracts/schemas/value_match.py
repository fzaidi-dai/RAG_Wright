"""KG-5a: subsumption-aware, canonicalized matching of a query property constraint to a clause's value.

The typed-KG constraint match (Leg B, 4c) was brittle -- exact string set-intersection -- so it lost signal
to (1) surface variants of the same jurisdiction and (2) values at a finer granularity than the query. This
module is the fix: `value_satisfies(dimension, query_value, clause_value)` decides whether a clause's stored
value satisfies a query constraint, using

  - JURISDICTION: canonicalization (England/England and Wales/English law -> england), else normalized string;
  - SUBSUMPTION rollup: a more specific closed value satisfies a query for its broader value
    (licensor_affiliates -> affiliates; geographic_and_activity -> geographic AND activity;
     price_and_terms -> price AND terms);
  - everything else: exact match.

Additive: no re-extraction, no value-node mutation -- just canonicalization + a small ontology hierarchy
(also recorded as skos:broader in contract_bridge.ttl). FUTURE (deferred): covered_subject
{trademark, copyright} -> ip_infringement (legally sound, slightly looser -- add if a query needs it).
"""

from __future__ import annotations

from pydantic import BaseModel

from rag_wright.packs.contracts.schemas.jurisdiction import canonicalize_jurisdiction


class NormalizedValue(BaseModel):
    """CAP-REG-2: the canonical form of a typed value (KG-5a) — a surface value normalized (jurisdiction
    canonicalization / subsumption) for matching. `changed` is False when the surface was already canonical.
    The output contract of the `typed_value_normalization` capability."""

    dimension: str
    surface: str
    canonical: str
    changed: bool

# a more-specific closed value -> the broader query value(s) it also satisfies (the three clear cases)
VALUE_ROLLUP: dict[str, dict[str, set[str]]] = {
    "covered_parties": {
        "licensor_affiliates": {"affiliates"},
        "licensee_affiliates": {"affiliates"},
    },
    "restriction_scope": {
        "geographic_and_activity": {"geographic", "activity"},
    },
    "mfn_scope": {
        "price_and_terms": {"price", "terms"},
    },
}


def satisfied_values(dimension: str, clause_value: str) -> set[str]:
    """The query-constraint values a clause value satisfies: itself plus any broader value it rolls up to."""
    return {clause_value} | VALUE_ROLLUP.get(dimension, {}).get(clause_value, set())


def _norm_jurisdiction(v: str) -> str:
    return canonicalize_jurisdiction(v) or (v or "").strip().lower()


def value_satisfies(dimension: str, query_value: str, clause_value: str) -> bool:
    """Whether a clause's stored `clause_value` satisfies the query's `query_value` on this dimension --
    jurisdiction-canonicalized, subsumption-aware, exact otherwise."""
    if dimension == "jurisdiction":
        return _norm_jurisdiction(query_value) == _norm_jurisdiction(clause_value)
    return query_value in satisfied_values(dimension, clause_value)


def constraint_match_count(query_constraints: set, clause_props: set) -> int:
    """How many of the query's (dimension, value) constraints the clause's (dimension, value) props satisfy,
    under canonicalization + subsumption. Replaces the old exact set-intersection count."""
    return sum(
        1 for qd, qv in query_constraints
        if any(cd == qd and value_satisfies(qd, qv, cv) for cd, cv in clause_props)
    )


def register_typed_value_normalization(registry) -> None:
    """CAP-REG-2: register `typed_value_normalization` (function; KG-5a canonicalization + subsumption)."""
    registry.register(
        "typed_value_normalization",
        contract=NormalizedValue,
        kind="function",
        display_name="Typed value normalization",
    )
