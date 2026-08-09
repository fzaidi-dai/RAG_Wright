"""T57 (FR-C.6, ADR-0025): the retrieval FUNCTION taxonomy -- a superset of the 41 CUAD categories."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_wright.contracts.function import (
    FUNCTION_LABEL_SET,
    FUNCTION_LABELS,
    ExtendedFunction,
    FunctionConfidence,
    FunctionScore,
    canonical_function,
    primary_function,
)
from rag_wright.contracts.ontology import ClauseCategory


def test_function_labels_are_cuad_41_plus_the_3_extensions():
    assert len(FUNCTION_LABELS) == len(ClauseCategory) + len(ExtendedFunction) == 44
    # the 41 CUAD categories are all present (by value, no drift)
    assert {c.value for c in ClauseCategory} <= FUNCTION_LABEL_SET
    # the 3 ACORD families CUAD lacks are present
    assert {"Indemnification", "Indirect/Consequential Damages Waiver", "Warranty Disclaimer"} <= FUNCTION_LABEL_SET
    # a representative CUAD type the classifier already separates
    assert "Cap On Liability" in FUNCTION_LABEL_SET


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


def test_labels_are_unique_and_order_is_cuad_then_extensions():
    assert len(FUNCTION_LABELS) == len(set(FUNCTION_LABELS))  # no duplicate label collides CUAD/extension
    assert FUNCTION_LABELS[: len(ClauseCategory)] == tuple(c.value for c in ClauseCategory)
    assert FUNCTION_LABELS[len(ClauseCategory) :] == tuple(f.value for f in ExtendedFunction)


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
