"""T57 (FR-C.6, ADR-0025): the retrieval FUNCTION taxonomy -- a superset of the 41 CUAD categories."""

from __future__ import annotations

from rag_wright.contracts.function import (
    FUNCTION_LABEL_SET,
    FUNCTION_LABELS,
    ExtendedFunction,
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


def test_none_sentinel_is_not_a_function_type():
    # NONE is the classifier's off-taxonomy sentinel, not a function -- it must not be a label
    assert "NONE" not in FUNCTION_LABEL_SET


def test_labels_are_unique_and_order_is_cuad_then_extensions():
    assert len(FUNCTION_LABELS) == len(set(FUNCTION_LABELS))  # no duplicate label collides CUAD/extension
    assert FUNCTION_LABELS[: len(ClauseCategory)] == tuple(c.value for c in ClauseCategory)
    assert FUNCTION_LABELS[len(ClauseCategory) :] == tuple(f.value for f in ExtendedFunction)
