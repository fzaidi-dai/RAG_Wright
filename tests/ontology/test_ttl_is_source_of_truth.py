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

# ADR-0066 P2 note: the applicability / cardinality / deontic-polarity constants were DELETED from
# symbolic_validation.py -- those now live ONLY in the ttl and are exercised (ttl-sourced) by
# tests/spans/test_symbolic_validation.py. What remains here are the two drift-checks whose Python target still
# exists: CLOSED_VOCAB (generated from the ttl, P1a) and VALUE_ROLLUP (still Python-authored, until a later phase).

_VIEW = load_contract_ontology()


def test_closed_vocab_matches_property_contract() -> None:
    py = {dim.value: set(vocab) for dim, vocab in CLOSED_VOCAB.items()}
    assert _VIEW.closed_vocab == py


def test_value_rollup_matches() -> None:
    py = {dim: {val: set(broaders) for val, broaders in mapping.items()}
          for dim, mapping in VALUE_ROLLUP.items()}
    assert _VIEW.value_rollup == py
