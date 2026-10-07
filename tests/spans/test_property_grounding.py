"""T61 (FR-C.6, ADR-0028): the deterministic property-grounding judge. Hermetic, no model."""

from __future__ import annotations

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.packs.contracts.schemas.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.packs.contracts.spans.property_grounding import (
    is_grounded,
    needs_escalation,
    reground,
    ungrounded_assertions,
)

_PROV = Provenance.of(ChunkId.of("doc", 0, "clause body"))


def _record(function: str, *assertions: tuple) -> ClausePropertyRecord:
    return ClausePropertyRecord(
        clause_id=str(_PROV.chunk_id), function=function,
        assertions=[PropertyAssertion(provenance=_PROV, confidence=c, dimension=d, value=v)
                    for d, v, c in assertions],
    )


def test_lexically_anchored_value_requires_its_cue():
    assert is_grounded(PropertyDimension.CARVE_OUT, "fraud", "arising from fraud or theft")
    assert not is_grounded(PropertyDimension.CARVE_OUT, "fraud", "in no event liable for consequential damages")


def test_semantic_dimension_is_always_grounded():
    # mutuality carries no keyword -> the judge cannot disprove it -> treated as grounded
    assert is_grounded(PropertyDimension.MUTUALITY, "unilateral", "anything at all")
    assert is_grounded(PropertyDimension.FAVORABILITY, "seller_favorable", "anything at all")


def test_flags_flashs_fraud_hallucination_but_not_the_grounded_carveouts():
    text = "except for a party's indemnification obligations or its breach of confidentiality"  # no "fraud"
    rec = _record(
        "Uncapped Liability",
        (PropertyDimension.CARVE_OUT, "confidentiality", ConfidenceTag.EXTRACTED),
        (PropertyDimension.CARVE_OUT, "indemnification", ConfidenceTag.EXTRACTED),
        (PropertyDimension.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),  # the hallucination
    )
    flagged = ungrounded_assertions(rec, text)
    assert [a.value for a in flagged] == ["fraud"]
    assert needs_escalation(rec, text) is True


def test_inferred_and_ambiguous_are_not_flagged():
    text = "no fraud mentioned here"  # cue absent, but these are not claimed as stated
    rec = _record(
        "Cap On Liability",
        (PropertyDimension.CARVE_OUT, "gross_negligence", ConfidenceTag.INFERRED),
        (PropertyDimension.CARVE_OUT, "act_of_god", ConfidenceTag.AMBIGUOUS),
    )
    assert ungrounded_assertions(rec, text) == []
    assert needs_escalation(rec, text) is False


def test_reground_downgrades_only_the_ungrounded_extracted():
    text = "liability excludes confidentiality breaches"  # has confidentiality, no fraud
    rec = _record(
        "Uncapped Liability",
        (PropertyDimension.CARVE_OUT, "confidentiality", ConfidenceTag.EXTRACTED),  # grounded -> kept
        (PropertyDimension.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),  # ungrounded -> AMBIGUOUS
        (PropertyDimension.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),  # semantic -> kept
    )
    gated = reground(rec, text)
    by = {(a.dimension, a.value): a.confidence for a in gated.assertions}
    assert by[(PropertyDimension.CARVE_OUT, "confidentiality")] is ConfidenceTag.EXTRACTED
    assert by[(PropertyDimension.CARVE_OUT, "fraud")] is ConfidenceTag.AMBIGUOUS  # downgraded
    assert by[(PropertyDimension.MUTUALITY, "mutual")] is ConfidenceTag.EXTRACTED  # semantic untouched


# --- GROUNDING-OPENVALUED (ADR-0040): open-valued scalars are token-checked ---


def test_open_valued_jurisdiction_with_no_textual_basis_is_flagged():
    # jurisdiction is open-valued: a value whose tokens never appear in the clause is a fabrication
    text = "this agreement is governed by the laws of the State of New York"
    assert not is_grounded(PropertyDimension.JURISDICTION, "delaware", text)
    assert is_grounded(PropertyDimension.JURISDICTION, "new_york", text)  # both tokens present


def test_open_valued_numeric_normalization_survives_via_a_unit_token():
    # a normalized value like 12_months is grounded by the "months" token even when the digit is spelled out
    assert is_grounded(PropertyDimension.TEMPORAL_BOUND, "12_months", "for a period of twelve (12) months")
    assert is_grounded(PropertyDimension.NOTICE_PERIOD, "30_days", "upon thirty days prior written notice")
    assert not is_grounded(PropertyDimension.NOTICE_PERIOD, "30_days", "either party may terminate at will")


def test_open_valued_ungrounded_is_downgraded_but_grounded_is_kept():
    text = "audited annually by an independent accountant; governed by the laws of England"
    rec = _record(
        "Audit Rights",
        (PropertyDimension.AUDIT_FREQUENCY, "annual", ConfidenceTag.EXTRACTED),  # "annual" in "annually" -> kept
        (PropertyDimension.JURISDICTION, "california", ConfidenceTag.EXTRACTED),  # no basis -> downgraded
    )
    by = {a.dimension: a.confidence for a in reground(rec, text).assertions}
    assert by[PropertyDimension.AUDIT_FREQUENCY] is ConfidenceTag.EXTRACTED
    assert by[PropertyDimension.JURISDICTION] is ConfidenceTag.AMBIGUOUS


def test_semantic_closed_dim_is_not_token_checked():
    # mutuality is a closed SEMANTIC dim (Layer 3's job), NOT open-valued: the word "mutual" need not appear
    assert is_grounded(PropertyDimension.MUTUALITY, "mutual", "each party shall indemnify the other")
    assert is_grounded(PropertyDimension.CAP_BASIS, "multiple_of_fees", "liability is limited to the amounts paid")
