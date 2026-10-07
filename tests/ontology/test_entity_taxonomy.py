"""DD-7 (ADR-0066): the entity-graph taxonomy is ttl-sourced. The loader reads it from `contract_bridge.ttl`,
codegen renders it into `_generated_vocab.py` (drift caught by `test_generated_vocab_in_sync`), and
`contract_taxonomy` re-exports the generated constants -- so the values live in the ttl, never hardcoded in code.
"""
from __future__ import annotations

import inspect

from rag_wright.packs.contracts.ontology import contract_taxonomy as T
from rag_wright.packs.contracts.ontology.loader import load_contract_ontology


def test_loader_reads_the_entity_taxonomy_from_the_ttl():
    view = load_contract_ontology()
    assert view.entity_types == {"Organization", "Person"}
    assert view.relationship_types == {"Contracts With", "Affiliate Of"}


def test_contract_taxonomy_reexports_match_the_ttl():
    view = load_contract_ontology()
    assert T.ENTITY_TYPES == view.entity_types
    assert T.RELATIONSHIP_TYPES == view.relationship_types
    # the named constants ARE the ttl labels (ergonomic surface), sourced not authored
    assert {T.ORGANIZATION, T.PERSON} == view.entity_types
    assert {T.CONTRACTS_WITH, T.AFFILIATE_OF} == view.relationship_types


def test_contract_taxonomy_hardcodes_no_taxonomy_values():
    # DD-7 end-state: the module SOURCES the values (re-export), it does not author them -- so no taxonomy string
    # literal appears in it (a re-added literal would re-open the code-authoritative debt this closed).
    src = inspect.getsource(T)
    for value in ("Organization", "Person", "Contracts With", "Affiliate Of"):
        assert value not in src, f"contract_taxonomy must not hardcode {value!r} -- source it from the ttl"
