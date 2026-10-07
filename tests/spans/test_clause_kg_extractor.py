"""KG-2 (FR-C.6, ADR-0033/0028): the Clause -> ClausePropertyRecord adapter + the grounding gate.

Hermetic tests drive the pure mapping and the gate with hand-built `Clause` instances (no model call).
The live smoke (`-m model`) runs real granite-4.2-8b on a real clause.
"""

from __future__ import annotations

import pytest

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.packs.contracts.schemas.property import CLOSED_VOCAB, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.packs.contracts.ontology import clause_template as ct
from rag_wright.packs.contracts.spans.clause_kg_extractor import (
    DGClausePropertyExtractor,
    clause_to_record,
)

_D = PropertyDimension
_CID = ChunkId.of("contract-x", 0, "cap clause text")


def _record(clause: ct.Clause, function: str = "Cap On Liability"):
    return clause_to_record(clause, chunk_id=_CID, function=function, span_id="span-1")


def _by_dim(record):
    out: dict[PropertyDimension, list[str]] = {}
    for a in record.assertions:
        out.setdefault(a.dimension, []).append(a.value)
    return out


def test_empty_clause_yields_no_assertions() -> None:
    """A Clause with every field at its OTHER/None default asserts nothing (OTHER = 'not asserted')."""
    rec = _record(ct.Clause(document_reference="8.1"))
    assert rec.assertions == []
    assert rec.clause_id == str(_CID)
    assert rec.function == "Cap On Liability"
    # persist-clause-span-id: even a PROPERTY-LESS clause carries its operative span_id (1:1), so it is never
    # left with only a bare function label at query time (the A1 fix).
    assert rec.span_id == "span-1"


def test_scalar_enums_map_to_their_dimensions() -> None:
    clause = ct.Clause(
        document_reference="8.1",
        has_mutuality=ct.Mutuality.MUTUAL,
        has_favorability=ct.Favorability.SELLER_FAVORABLE,
        covers_party_scope=ct.PartyScope.AFFILIATES,
    )
    by = _by_dim(_record(clause))
    assert by[_D.MUTUALITY] == ["mutual"]
    assert by[_D.FAVORABILITY] == ["seller_favorable"]
    assert by[_D.COVERED_PARTIES] == ["affiliates"]


def test_other_escape_is_dropped() -> None:
    """An explicit OTHER (the LLM found no vocab match) is not asserted."""
    clause = ct.Clause(document_reference="1", has_mutuality=ct.Mutuality.OTHER)
    assert _record(clause).assertions == []


def test_list_dimensions_expand_to_multiple_assertions() -> None:
    clause = ct.Clause(
        document_reference="9",
        covers=[ct.Subject.TRADEMARK, ct.Subject.COPYRIGHT],
        excepts=[ct.ExceptionModel.FRAUD, ct.ExceptionModel.WILLFUL_MISCONDUCT],
    )
    by = _by_dim(_record(clause))
    assert sorted(by[_D.COVERED_SUBJECT]) == ["copyright", "trademark"]
    assert sorted(by[_D.CARVE_OUT]) == ["fraud", "willful_misconduct"]


def test_open_list_dims_retain_verbatim_when_out_of_vocab() -> None:
    # issue 0037: on the genuinely-OPEN descriptive list-dims (carve_out / damage_type), an out-of-vocab value is
    # RETAINED VERBATIM, not collapsed to OTHER and discarded. Canonical members still normalize.
    clause = ct.Clause(
        document_reference="1",
        excepts=["confidentiality", "loss of profits"],             # canonical + out-of-vocab
        prohibits_damage=["consequential", "reputational harm"],    # canonical + genuinely unmapped -> verbatim
    )
    by = _by_dim(_record(clause))
    assert sorted(by[_D.CARVE_OUT]) == ["confidentiality", "loss of profits"]  # nothing dropped
    assert "consequential" in by[_D.DAMAGE_TYPE] and "reputational harm" in by[_D.DAMAGE_TYPE]


def test_covered_subject_is_closed_out_of_vocab_dropped() -> None:
    # issue 0040: `covered_subject` (Subject) is a CLOSED conduct vocab, NOT open. A conduct value is kept; an
    # out-of-vocab value ('API', a party name -- the model answering "what does this cover?") is DROPPED, not
    # retained as AMBIGUOUS noise. Reverts 0037 for this dim only (carve_out/damage_type stay open above).
    clause = ct.Clause(document_reference="1", covers=["fraud", "API", "HOVIONE", "gross negligence claims"])
    by = _by_dim(_record(clause))
    assert sorted(by.get(_D.COVERED_SUBJECT, [])) == ["fraud", "gross_negligence"]  # conduct kept; noise dropped


def test_open_list_keyword_maps_a_quoted_phrase_at_the_boundary() -> None:
    # issue 0037: a carve-out the model quoted verbatim maps onto its canonical value at the boundary (keyword
    # fallback) -- what the old Clause validator did, now done here so the raw phrase is still available to keep.
    clause = ct.Clause(
        document_reference="1",
        excepts=["Except in respect of the Supplier's indemnification obligations under clause 8"],
        prohibits_damage=["consequential damages of any kind"],
    )
    by = _by_dim(_record(clause))
    assert by[_D.CARVE_OUT] == ["indemnification"]     # keyword-mapped to the canonical member
    assert by[_D.DAMAGE_TYPE] == ["consequential"]     # keyword-mapped to the canonical member


def test_damage_type_synonym_maps_to_canonical() -> None:
    # issue 0037 (Part 2): a known damage-waiver synonym maps to its canonical DamageType via the ontology
    # skos:broader map, while an unmapped one is kept verbatim (both retained -- nothing lost).
    clause = ct.Clause(document_reference="1", prohibits_damage=["loss of profits", "reputational harm"])
    vals = _by_dim(_record(clause))[_D.DAMAGE_TYPE]
    assert "consequential" in vals            # 'loss of profits' -> consequential (skos:broader synonym)
    assert "reputational harm" in vals         # no mapping -> verbatim kept


def test_query_constraint_record_uses_the_no_function_sentinel() -> None:
    # A3 bug fix: a QUERY has no clause function, so the query-constraint path builds a ClausePropertyRecord with
    # the NO_FUNCTION sentinel. function="" used to fail ClausePropertyRecord validation -> the query-constraint
    # extraction ERRORED -> the whole cross_corpus leg degraded to empty constraints (every span match=0.0).
    from rag_wright.packs.contracts.schemas.function import NO_FUNCTION
    from rag_wright.packs.contracts.spans.clause_kg_extractor import clause_to_record

    clause = ct.Clause(
        document_reference="q", caps=ct.CapConstraint(cap_basis=ct.CapBasis.MULTIPLE_OF_FEES, cap_operator="eq"))
    rec = clause_to_record(clause, chunk_id=_CID, function=NO_FUNCTION, span_id="")
    assert rec.function == NO_FUNCTION  # valid: the no-function sentinel (was a ValidationError for "")
    assert (_D.CAP_BASIS, "multiple_of_fees") in {(a.dimension, a.value) for a in rec.assertions}  # props survive


def test_cap_constraint_maps_basis_and_open_quantum() -> None:
    """caps -> CAP_BASIS (cap_other -> canonical 'other') + CAP_QUANTUM (open literal)."""
    clause = ct.Clause(
        document_reference="8.1",
        caps=ct.CapConstraint(cap_basis=ct.CapBasis.CAP_OTHER, cap_operator="lteq", cap_quantum="12_months"),
    )
    by = _by_dim(_record(clause))
    assert by[_D.CAP_BASIS] == ["other"]  # cap_other -> other (the documented KG-1 divergence)
    assert by[_D.CAP_QUANTUM] == ["12_months"]


def test_temporal_kind_routes_to_notice_period_or_term() -> None:
    notice = ct.Clause(
        document_reference="12",
        bounded_by=ct.TemporalConstraint(temporal_kind="notice_period", temporal_duration="30_days"),
    )
    term = ct.Clause(
        document_reference="2",
        bounded_by=ct.TemporalConstraint(temporal_kind="term", temporal_duration="12_months"),
    )
    assert _by_dim(_record(notice))[_D.NOTICE_PERIOD] == ["30_days"]
    assert _by_dim(_record(term))[_D.TEMPORAL_BOUND] == ["12_months"]


def test_empty_temporal_duration_is_skipped() -> None:
    clause = ct.Clause(
        document_reference="2",
        bounded_by=ct.TemporalConstraint(temporal_kind="term", temporal_duration="   "),
    )
    assert _record(clause).assertions == []


def test_jurisdiction_maps_open_name_and_multiplicity() -> None:
    clause = ct.Clause(
        document_reference="15",
        governed_by=ct.Jurisdiction(jurisdiction_name="New York", law_multiplicity=ct.LawMultiplicity.SINGLE),
    )
    by = _by_dim(_record(clause, function="Governing Law"))
    assert by[_D.JURISDICTION] == ["New York"]
    assert by[_D.LAW_MULTIPLICITY] == ["single"]


def test_every_closed_value_emitted_is_in_the_property_vocab() -> None:
    """Adapter output stays on the shared vocabulary: every closed-dim value validates against CLOSED_VOCAB
    (PropertyAssertion would already reject an out-of-vocab EXTRACTED value; this asserts it explicitly)."""
    clause = ct.Clause(
        document_reference="8.1",
        has_mutuality=ct.Mutuality.UNILATERAL,
        prohibits_damage=[ct.DamageType.CONSEQUENTIAL, ct.DamageType.PUNITIVE],
        caps=ct.CapConstraint(cap_basis=ct.CapBasis.MULTIPLE_OF_FEES),
    )
    for a in _record(clause).assertions:
        vocab = CLOSED_VOCAB.get(a.dimension)
        if vocab is not None:
            assert a.value in vocab


def test_grounding_gate_downgrades_ungrounded_extracted_value() -> None:
    """The ADR-0028 gate: an EXTRACTED carve_out=fraud on a clause with no 'fraud' cue -> AMBIGUOUS."""
    extractor = DGClausePropertyExtractor(
        lambda text: ct.Clause(document_reference="8", excepts=[ct.ExceptionModel.FRAUD])
    )
    rec = extractor(chunk_id=_CID, function="Cap On Liability", text="Liability is capped at fees paid.", span_id="s")
    fraud = [a for a in rec.assertions if a.value == "fraud"]
    assert len(fraud) == 1
    assert fraud[0].confidence == ConfidenceTag.AMBIGUOUS  # cue 'fraud' absent -> downgraded


def test_grounding_gate_keeps_grounded_value_extracted() -> None:
    extractor = DGClausePropertyExtractor(
        lambda text: ct.Clause(document_reference="8", excepts=[ct.ExceptionModel.FRAUD])
    )
    rec = extractor(chunk_id=_CID, function="Cap On Liability", text="except in the case of fraud", span_id="s")
    fraud = [a for a in rec.assertions if a.value == "fraud"]
    assert fraud[0].confidence == ConfidenceTag.EXTRACTED  # cue present -> stays EXTRACTED


def test_none_extraction_yields_empty_valid_record() -> None:
    extractor = DGClausePropertyExtractor(lambda text: None)
    rec = extractor(chunk_id=_CID, function="Cap On Liability", text="anything", span_id="s")
    assert rec.assertions == []
    assert rec.clause_id == str(_CID)


@pytest.mark.model
def test_live_granite_extracts_a_cap_clause() -> None:
    """Live smoke: real granite-4.2-8b on a mutual liability cap -> plausible typed properties."""
    from rag_wright.packs.contracts.spans.clause_kg_extractor import granite_clause_extractor

    text = (
        "Except in the case of fraud or gross negligence, in no event shall either party's aggregate "
        "liability under this Agreement exceed the total fees paid in the twelve (12) months preceding "
        "the claim. Neither party shall be liable for any indirect, consequential, or punitive damages."
    )
    rec = granite_clause_extractor()(
        chunk_id=ChunkId.of("live-contract", 3, text), function="Cap On Liability", text=text, span_id="s0"
    )
    by = _by_dim(rec)
    # the cap and a damage waiver should surface; exact set is model-dependent, so assert the shape
    assert rec.assertions, "granite returned no assertions for a clearly-capped clause"
    assert _D.CAP_QUANTUM in by or _D.CAP_BASIS in by or _D.DAMAGE_TYPE in by
