"""ADR-0066 Phase 0 gate: `contract_bridge.ttl` is the COMPLETE, authoritative source of the contract domain
knowledge. The loader parses the ttl and MUST reproduce today's Python constants EXACTLY -- proving the ontology
is complete and faithful BEFORE Phase 1 flips the generation direction (Python generated FROM the ttl).

Until Phase 1, the Python constants are still authoritative at runtime; this test is the equivalence proof that
lets Phase 1 reverse the arrow safely. When it passes, the `.ttl` alone carries: the closed vocabularies, the
scalar/list cardinality, the function -> applicable-dimensions applicability, the deontic polarity + restrictive
functions, and the value rollups.
"""

from __future__ import annotations

from rag_wright.contracts.property import CLOSED_VOCAB
from rag_wright.contracts.value_match import VALUE_ROLLUP
from rag_wright.ontology.loader import load_contract_ontology
from rag_wright.spans.symbolic_validation import (
    FUNCTION_APPLICABLE_DIMS,
    MULTI_VALUED_DIMENSIONS,
    PERMISSION_POLARITY_VALUES,
    RESTRICTIVE_FUNCTIONS,
)

_VIEW = load_contract_ontology()


def test_closed_vocab_matches_property_contract() -> None:
    py = {dim.value: set(vocab) for dim, vocab in CLOSED_VOCAB.items()}
    assert _VIEW.closed_vocab == py


def test_cardinality_matches_multi_valued_dimensions() -> None:
    py = {dim.value for dim in MULTI_VALUED_DIMENSIONS}
    assert _VIEW.multivalued == py


def test_function_applicable_dims_match_the_shacl_shapes() -> None:
    py = {fn: {d.value for d in dims} for fn, dims in FUNCTION_APPLICABLE_DIMS.items()}
    assert _VIEW.function_applicable_dims == py


def test_deontic_polarity_and_restrictive_functions_match() -> None:
    py_polarity = {dim.value: set(vals) for dim, vals in PERMISSION_POLARITY_VALUES.items()}
    assert _VIEW.permission_polarity == py_polarity
    assert _VIEW.restrictive_functions == set(RESTRICTIVE_FUNCTIONS)


def test_value_rollup_matches() -> None:
    py = {dim: {val: set(broaders) for val, broaders in mapping.items()}
          for dim, mapping in VALUE_ROLLUP.items()}
    assert _VIEW.value_rollup == py
