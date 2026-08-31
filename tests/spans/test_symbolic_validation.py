"""JUDGE-ONTOLOGY-1 (ADR-0040): the symbolic ontology-validation gate. Hermetic, no model, no network."""

from __future__ import annotations

from rag_wright.contracts.function import FUNCTION_LABEL_SET
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import ClausePropertyRecord, PropertyAssertion, PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.ontology.loader import load_contract_ontology
from rag_wright.spans.symbolic_validation import flagged_dimensions, symbolic_validate

_D = PropertyDimension
_PROV = Provenance.of(ChunkId.of("doc", 0, "clause body"))
# ADR-0066 P2: the applicability map now lives in the ttl (the source of truth); read it via the loader.
_APPLICABLE = load_contract_ontology().function_applicable_dims  # {function: {dim value}}
_MULTIVALUED = load_contract_ontology().multivalued              # {dim value}


def _record(function: str, *assertions: tuple) -> ClausePropertyRecord:
    return ClausePropertyRecord(
        clause_id=str(_PROV.chunk_id), function=function,
        assertions=[PropertyAssertion(provenance=_PROV, confidence=c, dimension=d, value=v)
                    for d, v, c in assertions],
    )


def test_the_map_covers_exactly_the_function_taxonomy():
    # ADR-0066 P2: the applicability map (in the ttl) covers EXACTLY the FUNCTION taxonomy -- no typo key outside
    # it, no missing label (every one of the 52 labels is modeled; there are no permissive gaps).
    assert set(_APPLICABLE) == FUNCTION_LABEL_SET


def test_the_8_taxonomy_gap_types_are_modeled_with_existing_dims():
    # ADR-0049 (1): the 8 taxonomy-gap clause types are modeled (not permissive), each with its existing-dimension
    # applicability -- now asserted against the ttl.
    from rag_wright.contracts.function import TaxonomyGapFunction

    for f in TaxonomyGapFunction:
        assert f.value in _APPLICABLE, f"{f.value} must be modeled"
    assert {_D.NOTICE_PERIOD.value, _D.TEMPORAL_BOUND.value, _D.MUTUALITY.value, _D.PARTY_ASYMMETRY.value,
            _D.TERMINATION_RIGHT.value} <= _APPLICABLE["Force Majeure"]
    assert {_D.MUTUALITY.value, _D.PARTY_ASYMMETRY.value, _D.TEMPORAL_BOUND.value} <= _APPLICABLE["Confidentiality"]


def test_ont2_new_dimensions_added_to_their_types():
    # ADR-0049 (2): each taxonomy-gap type's defining facet dimension is in its applicability set (from the ttl)
    assert _D.DISPUTE_METHOD.value in _APPLICABLE["Dispute Resolution"]
    assert _D.COLLATERAL_TYPE.value in _APPLICABLE["Security Interest"]
    assert _D.FORCE_MAJEURE_EVENT.value in _APPLICABLE["Force Majeure"]
    assert _D.ROYALTY_BASIS.value in _APPLICABLE["Royalties"]
    assert _D.CONFIDENTIALITY_EXCEPTION.value in _APPLICABLE["Confidentiality"]
    assert _D.CONDITION_TYPE.value in _APPLICABLE["Condition Precedent"]
    for d in (_D.COLLATERAL_TYPE, _D.FORCE_MAJEURE_EVENT, _D.CONFIDENTIALITY_EXCEPTION):
        assert d.value in _MULTIVALUED
    for d in (_D.DISPUTE_METHOD, _D.ROYALTY_BASIS, _D.CONDITION_TYPE):
        assert d.value not in _MULTIVALUED


def test_wrong_dimension_on_a_function_is_flagged_but_the_applicable_one_is_not():
    # the observed error class: nonsolicit_target (valid value, in-vocab) asserted on Anti-Assignment, where
    # only assignment_consent / party_asymmetry apply. The lexical judge cannot see this; the SHACL gate can.
    assert flagged_dimensions(
        _record(
            "Anti-Assignment",
            (_D.ASSIGNMENT_CONSENT, "consent_required", ConfidenceTag.EXTRACTED),
            (_D.NONSOLICIT_TARGET, "employees", ConfidenceTag.EXTRACTED),
        )
    ) == {_D.NONSOLICIT_TARGET}


def test_symbolic_validate_downgrades_only_the_nonapplicable_assertion():
    rec = symbolic_validate(
        _record(
            "Anti-Assignment",
            (_D.ASSIGNMENT_CONSENT, "consent_required", ConfidenceTag.EXTRACTED),
            (_D.NONSOLICIT_TARGET, "employees", ConfidenceTag.EXTRACTED),
        )
    )
    by_dim = {a.dimension: a.confidence for a in rec.assertions}
    assert by_dim[_D.ASSIGNMENT_CONSENT] == ConfidenceTag.EXTRACTED  # applicable -> untouched
    assert by_dim[_D.NONSOLICIT_TARGET] == ConfidenceTag.AMBIGUOUS  # type error -> downgraded


def test_a_fully_applicable_record_is_returned_unchanged():
    rec = _record(
        "Cap On Liability",
        (_D.CAP_BASIS, "fixed_fee", ConfidenceTag.EXTRACTED),
        (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),
        (_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),
    )
    out = symbolic_validate(rec)
    assert [a.confidence for a in out.assertions] == [ConfidenceTag.EXTRACTED] * 3


def test_metadata_function_rejects_any_property_dimension():
    # Document Name carries NO property dimensions -> any asserted dimension is a type error
    assert flagged_dimensions(
        _record("Document Name", (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED))
    ) == {_D.MUTUALITY}


def test_downgrade_is_confidence_independent_but_leaves_ambiguous_alone():
    # an INFERRED type error is still downgraded; an already-AMBIGUOUS one stays AMBIGUOUS (no double-work)
    rec = symbolic_validate(
        _record(
            "Governing Law",
            (_D.NONSOLICIT_TARGET, "employees", ConfidenceTag.INFERRED),  # wrong dim, INFERRED
            (_D.MFN_SCOPE, "price", ConfidenceTag.AMBIGUOUS),  # wrong dim, already AMBIGUOUS
            (_D.JURISDICTION, "new_york", ConfidenceTag.EXTRACTED),  # applicable
        )
    )
    by_dim = {a.dimension: a.confidence for a in rec.assertions}
    assert by_dim[_D.NONSOLICIT_TARGET] == ConfidenceTag.AMBIGUOUS
    assert by_dim[_D.MFN_SCOPE] == ConfidenceTag.AMBIGUOUS
    assert by_dim[_D.JURISDICTION] == ConfidenceTag.EXTRACTED


def test_all_assertions_of_a_nonapplicable_multivalued_dimension_are_downgraded():
    rec = symbolic_validate(
        _record(
            "Governing Law",
            (_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),  # wrong dim, value 1
            (_D.CARVE_OUT, "confidentiality", ConfidenceTag.EXTRACTED),  # wrong dim, value 2
            (_D.JURISDICTION, "england", ConfidenceTag.EXTRACTED),  # applicable
        )
    )
    downgraded = {a.value for a in rec.assertions if a.confidence == ConfidenceTag.AMBIGUOUS}
    assert downgraded == {"fraud", "confidentiality"}


def test_unmodeled_function_is_permissive():
    # a function absent from the map is not closed-validated (coverage is expanded deliberately, never guessed)
    rec = _record("Anti-Assignment", (_D.NONSOLICIT_TARGET, "employees", ConfidenceTag.EXTRACTED))
    unmodeled = rec.model_copy(update={"function": "Some Future Unmodeled Function"})
    assert flagged_dimensions(unmodeled) == set()
    assert symbolic_validate(unmodeled) is unmodeled  # identity: no change


def test_empty_record_is_a_noop():
    assert flagged_dimensions(_record("Anti-Assignment")) == set()


def test_scalar_dimension_with_two_conflicting_values_is_flagged_and_both_downgraded():
    # JUDGE-ONTOLOGY-2 cardinality: cap_basis is scalar (sh:maxCount 1). granite hedging two enum values for
    # it is a self-contradiction -> both are suspect and downgraded (we cannot tell which is right).
    rec = _record(
        "Cap On Liability",
        (_D.CAP_BASIS, "fixed_fee", ConfidenceTag.EXTRACTED),
        (_D.CAP_BASIS, "multiple_of_fees", ConfidenceTag.EXTRACTED),
        (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),  # applicable, single -> untouched
    )
    assert flagged_dimensions(rec) == {_D.CAP_BASIS}
    out = symbolic_validate(rec)
    by_val = {a.value: a.confidence for a in out.assertions}
    assert by_val["fixed_fee"] == ConfidenceTag.AMBIGUOUS
    assert by_val["multiple_of_fees"] == ConfidenceTag.AMBIGUOUS
    assert by_val["mutual"] == ConfidenceTag.EXTRACTED


def test_multivalued_dimension_with_several_values_is_not_a_cardinality_violation():
    # carve_out is multi-valued: a clause may carve out fraud AND confidentiality -> both kept
    rec = _record(
        "Cap On Liability",
        (_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),
        (_D.CARVE_OUT, "confidentiality", ConfidenceTag.EXTRACTED),
        (_D.CARVE_OUT, "gross_negligence", ConfidenceTag.EXTRACTED),
    )
    assert flagged_dimensions(rec) == set()
    assert [a.confidence for a in symbolic_validate(rec).assertions] == [ConfidenceTag.EXTRACTED] * 3


def test_permission_polarity_value_on_a_restrictive_function_is_a_deontic_inversion():
    # JUDGE-ONTOLOGY-3: the observed error -- assignment_consent=free (a "may freely assign" value) on an
    # Anti-Assignment clause (whose purpose is to RESTRICT assignment) is a deontic contradiction.
    rec = _record(
        "Anti-Assignment",
        (_D.ASSIGNMENT_CONSENT, "free", ConfidenceTag.EXTRACTED),
        (_D.PARTY_ASYMMETRY, "symmetric", ConfidenceTag.EXTRACTED),  # applicable, valid -> untouched
    )
    assert flagged_dimensions(rec) == {_D.ASSIGNMENT_CONSENT}
    by_dim = {a.dimension: a.confidence for a in symbolic_validate(rec).assertions}
    assert by_dim[_D.ASSIGNMENT_CONSENT] == ConfidenceTag.AMBIGUOUS
    assert by_dim[_D.PARTY_ASYMMETRY] == ConfidenceTag.EXTRACTED


def test_restriction_polarity_consent_value_on_a_restrictive_function_passes():
    # consent_required IS consistent with Anti-Assignment (it restricts) -> not flagged
    rec = _record("Anti-Assignment", (_D.ASSIGNMENT_CONSENT, "consent_required", ConfidenceTag.EXTRACTED))
    assert flagged_dimensions(rec) == set()
    assert symbolic_validate(rec) is rec


def test_coc_unrestricted_on_change_of_control_is_a_deontic_inversion():
    rec = _record("Change Of Control", (_D.COC_CONSENT, "unrestricted", ConfidenceTag.EXTRACTED))
    assert flagged_dimensions(rec) == {_D.COC_CONSENT}
    assert symbolic_validate(rec).assertions[0].confidence == ConfidenceTag.AMBIGUOUS
