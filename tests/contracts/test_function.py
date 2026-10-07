"""T57 (FR-C.6, ADR-0025): the retrieval FUNCTION taxonomy -- a superset of the 41 CUAD categories."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_wright.packs.contracts.schemas.function import (
    FUNCTION_LABEL_SET,
    FUNCTION_LABELS,
    ExtendedFunction,
    FunctionConfidence,
    FunctionScore,
    TaxonomyGapFunction,
    canonical_function,
    primary_function,
)
from rag_wright.packs.contracts.schemas.ontology import ClauseCategory


def test_function_labels_are_cuad_41_plus_3_extensions_plus_8_taxonomy_gaps():
    # ADR-0048 step 2: the full-corpus classifier surfaced 8 recurring out-of-taxonomy functions (curated
    # LLM-assisted with human oversight) -> the taxonomy grows 44 -> 52.
    assert len(FUNCTION_LABELS) == len(ClauseCategory) + len(ExtendedFunction) + len(TaxonomyGapFunction) == 52
    # the 41 CUAD categories are all present (by value, no drift)
    assert {c.value for c in ClauseCategory} <= FUNCTION_LABEL_SET
    # the 3 ACORD families CUAD lacks are present
    assert {"Indemnification", "Indirect/Consequential Damages Waiver", "Warranty Disclaimer"} <= FUNCTION_LABEL_SET
    # a representative CUAD type the classifier already separates
    assert "Cap On Liability" in FUNCTION_LABEL_SET


def test_taxonomy_gap_labels_are_the_8_curated_additions():
    # the 8 genuinely-new clause functions approved for ADD (distinct from CUAD/ACORD's 44)
    assert {f.value for f in TaxonomyGapFunction} == {
        "Confidentiality", "Royalties", "Payment Terms", "Dispute Resolution",
        "Record Retention", "Security Interest", "Condition Precedent", "Force Majeure",
    }
    assert {f.value for f in TaxonomyGapFunction} <= FUNCTION_LABEL_SET


def test_canonical_function_folds_curated_aliases():
    # ADR-0048 step 2: the curated FOLD map resolves recurring synonyms/variants to their canonical label.
    assert canonical_function("Limitation of Liability") == "Cap On Liability"
    assert canonical_function("Limitations of Liability") == "Cap On Liability"  # plural variant
    assert canonical_function("Assignment") == "Anti-Assignment"
    assert canonical_function("Termination For Cause") == "Termination For Convenience"
    assert canonical_function("Jurisdiction") == "Governing Law"
    assert canonical_function("Non-Solicit Of Employees") == "No-Solicit Of Employees"
    assert canonical_function("insurance requirement") == "Insurance"  # aliases are case-insensitive too
    # the royalty family consolidates into the new Royalties label; Milestone Payment -> Payment Terms
    assert canonical_function("Royalty Grant") == "Royalties"
    assert canonical_function("Royalty Obligation") == "Royalties"
    assert canonical_function("Milestone Payment") == "Payment Terms"
    # Right of First Refusal was wrongly DROPped by the LLM -> folded to the existing label
    assert canonical_function("Right of First Refusal") == "Rofr/Rofo/Rofn"


def test_canonical_function_rejects_the_llm_misfolds():
    # 'Confidentiality' is its OWN new label, NOT folded into Non-Disparagement (an LLM mis-fold we rejected)
    assert canonical_function("Confidentiality") == "Confidentiality"
    # 'Representations and Warranties' was mis-folded to Warranty Duration -> we leave it off-taxonomy (honest)
    assert canonical_function("Representations and Warranties") is None
    # a pure artifact stays off-taxonomy
    assert canonical_function("Section Heading") is None


def test_canonical_function_normalizes_classifier_casing():
    # the classifier emits CUAD's casing ('Ip'); the contract wants the canonical 'IP'
    assert canonical_function("Ip Ownership Assignment") == "IP Ownership Assignment"
    assert canonical_function("Joint Ip Ownership") == "Joint IP Ownership"
    assert canonical_function("cap on liability") == "Cap On Liability"  # any casing
    assert canonical_function("Indemnification") == "Indemnification"  # already canonical
    assert canonical_function("Not A Function") is None


def test_none_sentinel_is_not_a_function_type():
    # NONE is the classifier's off-taxonomy sentinel, not a function -- it must not be a label
    assert "NONE" not in FUNCTION_LABEL_SET


def test_labels_are_unique_and_order_is_cuad_then_extensions_then_gaps():
    assert len(FUNCTION_LABELS) == len(set(FUNCTION_LABELS))  # no duplicate label collides across the 3 groups
    n_cuad, n_ext = len(ClauseCategory), len(ExtendedFunction)
    assert FUNCTION_LABELS[:n_cuad] == tuple(c.value for c in ClauseCategory)
    assert FUNCTION_LABELS[n_cuad : n_cuad + n_ext] == tuple(f.value for f in ExtendedFunction)
    assert FUNCTION_LABELS[n_cuad + n_ext :] == tuple(f.value for f in TaxonomyGapFunction)


# --- INGEST-LLM-CLASSIFIER (ADR-0048): FunctionScore = a classified function + coarse confidence -------------


def test_function_confidence_is_ordinal_high_medium_low():
    assert {c.value for c in FunctionConfidence} == {"high", "medium", "low"}


def test_function_score_requires_a_canonical_label():
    fs = FunctionScore(function="Cap On Liability", confidence=FunctionConfidence.HIGH)
    assert fs.function == "Cap On Liability"
    assert fs.confidence is FunctionConfidence.HIGH
    with pytest.raises(ValidationError):  # a non-canonical label is rejected (strict contract, ADR-0048)
        FunctionScore(function="Not A Function", confidence=FunctionConfidence.LOW)
    with pytest.raises(ValidationError):  # NONE is the off-taxonomy sentinel, not a function
        FunctionScore(function="NONE", confidence=FunctionConfidence.HIGH)


def test_function_score_canonicalizes_casing_at_the_boundary():
    # the classifier may emit CUAD casing; the contract normalizes to the canonical label
    assert FunctionScore(function="ip ownership assignment", confidence=FunctionConfidence.MEDIUM).function \
        == "IP Ownership Assignment"


def test_primary_function_is_the_first_score():
    scores = [
        FunctionScore(function="Cap On Liability", confidence=FunctionConfidence.HIGH),
        FunctionScore(function="Indemnification", confidence=FunctionConfidence.MEDIUM),
    ]
    assert primary_function(scores) == "Cap On Liability"
    assert primary_function([]) is None
