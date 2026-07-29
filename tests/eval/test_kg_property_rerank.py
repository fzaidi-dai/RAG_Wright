"""KG-5: the typed-KG constraint-match feature (pure; no store, no LLM)."""

from __future__ import annotations

from eval.kg_property_rerank import property_match

_EDGES = [
    {"dimension": "mutuality", "value": "mutual", "confidence": "EXTRACTED"},
    {"dimension": "carve_out", "value": "fraud", "confidence": "AMBIGUOUS"},
    {"dimension": "damage_type", "value": "consequential", "confidence": "EXTRACTED"},
]


def test_counts_matching_constraints() -> None:
    qc = {("mutuality", "mutual"), ("damage_type", "consequential"), ("cap_basis", "fixed_fee")}
    assert property_match(qc, _EDGES) == 2  # mutual + consequential match; fixed_fee not present


def test_no_match() -> None:
    assert property_match({("cap_basis", "fixed_fee")}, _EDGES) == 0


def test_empty_constraints_is_zero() -> None:
    assert property_match(set(), _EDGES) == 0


def test_grounded_only_drops_ambiguous() -> None:
    qc = {("carve_out", "fraud"), ("mutuality", "mutual")}
    assert property_match(qc, _EDGES) == 2  # both match when AMBIGUOUS counted
    assert property_match(qc, _EDGES, grounded_only=True) == 1  # fraud is AMBIGUOUS -> dropped
