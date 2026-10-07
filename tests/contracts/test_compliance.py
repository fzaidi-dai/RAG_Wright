"""CC-1 (compliance rung 1, roadmap §13.1): the Requirement/Claim contracts + compliance_bridge.ttl.

The regulatory-side `Requirement` (a single deontic rule) and the subject-side `Claim` (a checkable ad
assertion) contracts, the thin authored ad vocab (closed `ClaimType`/`DeonticType`/`Severity`), and the
`Constraint` applicability pair whose `(dimension, value)` shape matches the retrieval router (so CC-6's
applicability match reuses Leg-B). Provenance + confidence on everything (FR-S.4); no claim without a
citation (FR-Q.6). The sibling `compliance_bridge.ttl` grounds the schema on the public deontic backbone.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_wright.packs.compliance.schemas.compliance import (
    BRIDGE_TTL_PATH,
    Claim,
    ClaimType,
    Constraint,
    DeonticType,
    Requirement,
    Severity,
)
from rag_wright.contracts.provenance import ConfidenceTag


# --- enums: thin authored closed vocab -----------------------------------------------------------


def test_deontic_type_is_the_lkif_odrl_backbone():
    assert {d.value for d in DeonticType} == {"obligation", "prohibition", "permission"}


def test_claim_type_is_a_closed_vocab():
    assert "health" in {c.value for c in ClaimType} and "endorsement" in {c.value for c in ClaimType}


def test_severity_closed():
    assert {s.value for s in Severity} == {"low", "med", "high"}


# --- Constraint: (dimension, value) applicability pair, retrieval-tuple compatible ----------------


def test_constraint_is_frozen_and_tuple_compatible():
    c = Constraint(dimension="claim_type", value="health")
    assert c.as_tuple() == ("claim_type", "health")
    with pytest.raises(ValidationError):  # frozen
        c.dimension = "medium"


# --- Requirement --------------------------------------------------------------------------------


def _req(**over) -> Requirement:
    base = dict(
        source="FTC 16 CFR 255",
        citation="§ 255.5",
        deontic_type=DeonticType.OBLIGATION,
        actor="advertiser",
        applicability_scope=[Constraint(dimension="claim_type", value="endorsement")],
        requirement_text="A material connection between an endorser and the advertiser must be disclosed.",
    )
    base.update(over)
    base.setdefault("requirement_id", Requirement.make_id(base["source"], "255.5", base["requirement_text"]))
    return Requirement(**base)


def test_requirement_id_scheme_is_source_section_hash_and_deterministic():
    rid1 = Requirement.make_id("FTC 16 CFR 255", "255.5", "disclose material connections")
    rid2 = Requirement.make_id("FTC 16 CFR 255", "255.5", "disclose material connections")
    assert rid1 == rid2  # deterministic (content-hash gate, RAC-1)
    parts = rid1.split(":")
    assert len(parts) == 3 and parts[1] == "255.5" and len(parts[2]) == 16  # <source>:<section>:<hash16>
    assert Requirement.make_id("FTC 16 CFR 255", "255.5", "OTHER text") != rid1  # text changes the hash


def test_requirement_round_trips_and_defaults_extracted():
    r = _req()
    assert r.confidence is ConfidenceTag.EXTRACTED  # FR-S.4 graph-derived default
    again = Requirement.model_validate_json(r.model_dump_json())
    assert again == r
    assert again.applicability_scope[0].as_tuple() == ("claim_type", "endorsement")


def test_requirement_rejects_empty_text_or_citation():
    with pytest.raises(ValidationError):
        _req(requirement_text="   ")
    with pytest.raises(ValidationError):
        _req(citation="")


def test_requirement_deontic_and_severity_are_closed():
    with pytest.raises(ValidationError):
        _req(deontic_type="mandate")  # not in the closed set
    assert _req(severity=Severity.HIGH).severity is Severity.HIGH


# --- Claim --------------------------------------------------------------------------------------


def _claim(**over) -> Claim:
    base = dict(
        source_doc="influencer_skincare_no_disclosure",
        claim_type=ClaimType.HEALTH,
        assertion_text="clinically proven to erase deep wrinkles in just 7 days",
    )
    base.update(over)
    base.setdefault("fact_id", Claim.make_id(base["source_doc"], 0, base["assertion_text"]))
    return Claim(**base)


def test_claim_id_scheme_and_determinism():
    cid1 = Claim.make_id("influencer_ad", 0, "clinically proven")
    cid2 = Claim.make_id("influencer_ad", 0, "clinically proven")
    assert cid1 == cid2
    parts = cid1.split(":")
    assert len(parts) == 3 and parts[1] == "0" and len(parts[2]) == 16  # <source_doc>:<index>:<hash16>


def test_claim_round_trips_and_provenance_defaults():
    c = _claim(disclosures_present=[], evidence_referenced=False, medium="social")
    assert c.evidence_referenced is False and c.confidence is ConfidenceTag.EXTRACTED
    again = Claim.model_validate_json(c.model_dump_json())
    assert again == c


def test_claim_span_offsets_must_be_ordered():
    with pytest.raises(ValidationError):
        _claim(doc_start=50, doc_end=10)
    ok = _claim(doc_start=10, doc_end=50)
    assert ok.doc_start == 10 and ok.doc_end == 50


def test_claim_type_is_closed():
    with pytest.raises(ValidationError):
        _claim(claim_type="vibes")


# --- compliance_bridge.ttl: the sibling ontology -------------------------------------------------


def test_compliance_bridge_ttl_parses_and_declares_the_schema():
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph()
    g.parse(BRIDGE_TTL_PATH, format="turtle")  # must be valid Turtle
    text = BRIDGE_TTL_PATH.read_text(encoding="utf-8")
    # the deontic backbone + the two core classes are declared, grounded on public standards
    assert "odrl:" in text and "prov:" in text
    for term in ("Requirement", "Claim", "obligation", "prohibition", "permission"):
        assert term in text, f"compliance_bridge.ttl missing {term}"
    assert len(g) > 0  # non-empty graph


def test_compliance_report_ad_level_verdict_threshold():
    from rag_wright.packs.compliance.schemas.compliance import ComplianceReport, Verdict
    # >=2 violation findings -> hard VIOLATION
    assert ComplianceReport(source_doc="a", summary={"violation": 2, "needs_review": 5}).verdict is Verdict.VIOLATION
    # a LONE violation finding among many -> escalate, not hard-flag
    assert ComplianceReport(source_doc="a", summary={"violation": 1, "needs_review": 7}).verdict is Verdict.NEEDS_REVIEW
    # any needs_review with no violation -> escalate
    assert ComplianceReport(source_doc="a", summary={"needs_review": 3, "compliant": 10}).verdict is Verdict.NEEDS_REVIEW
    # nothing fired -> compliant
    assert ComplianceReport(source_doc="a", summary={"compliant": 16}).verdict is Verdict.COMPLIANT
