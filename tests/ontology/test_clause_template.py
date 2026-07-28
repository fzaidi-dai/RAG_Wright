"""KG-1 (FR-C, ADR-0033): the clause extraction template compiled from the bridge ontology.

These tests pin the *contract* the KG-2 extractor will populate: the Pydantic template compiled
(deterministically, zero-LLM) from `contract_bridge.ttl` via `docling-graph template from-ontology`.
The load-bearing invariant is that the template's closed vocabularies stay in lockstep with
`contracts/property.py::CLOSED_VOCAB` -- the extractor and the query-decomposer must share exactly one
vocabulary (CLAUDE.md standing rule / ADR-0026). If someone edits the ontology and the two drift, this
test fails.
"""

from __future__ import annotations

import enum
from pathlib import Path

from pydantic import BaseModel

from rag_wright.contracts.property import CLOSED_VOCAB, PropertyDimension
from rag_wright.ontology import clause_template as t

_TTL = Path(__file__).resolve().parents[2] / "src/rag_wright/ontology/contract_bridge.ttl"

# Template enum  ->  the PropertyDimension whose CLOSED_VOCAB it must equal (minus the OTHER escape).
# CapBasis is the one intentional divergence: OWL individuals cannot reuse the value "other" without
# clashing with the auto-added OTHER escape member, so the individual is `cap_other` and KG-3 maps it
# back to the canonical "other".
_ENUM_TO_DIMENSION = {
    t.Mutuality: PropertyDimension.MUTUALITY,
    t.Favorability: PropertyDimension.FAVORABILITY,
    t.ExceptionModel: PropertyDimension.CARVE_OUT,
    t.Subject: PropertyDimension.COVERED_SUBJECT,
    t.PartyScope: PropertyDimension.COVERED_PARTIES,
    t.Asymmetry: PropertyDimension.PARTY_ASYMMETRY,
    t.DamageType: PropertyDimension.DAMAGE_TYPE,
    t.WarrantyScope: PropertyDimension.WARRANTY_SCOPE,
    t.ClaimScope: PropertyDimension.CLAIM_SCOPE,
    t.ProceduralDuty: PropertyDimension.PROCEDURAL,
    t.LawMultiplicity: PropertyDimension.LAW_MULTIPLICITY,
    t.IpOwnership: PropertyDimension.IP_OWNERSHIP,
    t.NonsolicitTarget: PropertyDimension.NONSOLICIT_TARGET,
    t.RenewalMechanism: PropertyDimension.RENEWAL_MECHANISM,
    # tier 3 -- CUAD-family extensions (KG-4)
    t.ExclusivityType: PropertyDimension.EXCLUSIVITY_TYPE,
    t.RightOfFirstType: PropertyDimension.RIGHT_OF_FIRST_TYPE,
    t.RestrictionScope: PropertyDimension.RESTRICTION_SCOPE,
    t.CocConsent: PropertyDimension.COC_CONSENT,
    t.AssignmentConsent: PropertyDimension.ASSIGNMENT_CONSENT,
    t.EscrowReleaseTrigger: PropertyDimension.ESCROW_RELEASE_TRIGGER,
    t.MfnScope: PropertyDimension.MFN_SCOPE,
    t.TerminationRight: PropertyDimension.TERMINATION_RIGHT,
}


def _members(e: type[enum.Enum]) -> set[str]:
    """Enum values minus the compiler's auto-added OTHER escape."""
    return {m.value for m in e} - {"Other"}


def test_ttl_parses_with_rdflib() -> None:
    """The bridge ontology is well-formed OWL/Turtle (the KG-1 compile input)."""
    rdflib = __import__("rdflib")
    g = rdflib.Graph()
    g.parse(_TTL, format="turtle")
    assert len(g) > 100  # ~335 triples; guards against a truncated/empty file


def test_clause_is_the_root_model() -> None:
    assert issubclass(t.Clause, BaseModel)
    # the graph identity field the converter keys on
    assert "document_reference" in t.Clause.model_fields


def test_closed_vocab_matches_property_contract() -> None:
    """Every template enum equals its PropertyDimension's CLOSED_VOCAB (one shared vocabulary)."""
    for enum_cls, dim in _ENUM_TO_DIMENSION.items():
        assert _members(enum_cls) == set(CLOSED_VOCAB[dim]), (
            f"{enum_cls.__name__} drifted from CLOSED_VOCAB[{dim.value}]"
        )


def test_cap_basis_divergence_is_the_documented_one() -> None:
    """CapBasis == CLOSED_VOCAB[cap_basis] once cap_other is mapped back to the canonical 'other'."""
    mapped = {("other" if v == "cap_other" else v) for v in _members(t.CapBasis)}
    assert mapped == set(CLOSED_VOCAB[PropertyDimension.CAP_BASIS])


def test_every_enum_has_the_other_escape() -> None:
    """The AMBIGUOUS/other escape (ADR-0026) survives compilation on every closed vocab."""
    for enum_cls in _ENUM_TO_DIMENSION:
        assert "Other" in {m.value for m in enum_cls}


def test_multivalued_dimensions_are_lists() -> None:
    """Carve-outs, covered subjects, and waived damage types are sets on a clause -> list fields."""
    for field in ("covers", "excepts", "prohibits_damage"):
        assert "list" in str(t.Clause.model_fields[field].annotation).lower()


def test_scalar_dimensions_are_single() -> None:
    for field in ("has_mutuality", "has_favorability", "prohibits_solicit", "requires_duty"):
        ann = str(t.Clause.model_fields[field].annotation).lower()
        assert "list" not in ann


def test_odrl_constraint_models_present() -> None:
    """Q5 depth: cap + temporal bounds compile to nested odrl:Constraint models; jurisdiction too."""
    assert set(t.CapConstraint.model_fields) == {"cap_basis", "cap_operator", "cap_quantum"}
    assert set(t.TemporalConstraint.model_fields) == {
        "temporal_duration",
        "temporal_kind",
        "temporal_operator",
    }
    assert set(t.Jurisdiction.model_fields) == {"jurisdiction_name", "law_multiplicity"}
    # they hang off the Clause as optional edges
    for field in ("caps", "bounded_by", "governed_by"):
        assert field in t.Clause.model_fields


def test_clause_instantiates_minimally() -> None:
    """The template is a usable Pydantic model (identity is the only required field)."""
    c = t.Clause(document_reference="8.1 Limitation of Liability")
    assert c.document_reference == "8.1 Limitation of Liability"
