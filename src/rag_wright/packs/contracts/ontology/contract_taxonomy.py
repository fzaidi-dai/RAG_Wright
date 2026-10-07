"""The reference CONTRACT domain pack's entity-node + entity-relationship taxonomy (DD-5/DD-7, ADR-0066/0117).

De-domaining (DD-5): the engine's generic primitives + contracts are taxonomy-free -- `entity_type` and
`relationship_type` are plain strings the CALLER (a domain graph) names. The closed value sets below are DOMAIN
knowledge.

ADR-0066 end-state (DD-7): those values are now declared in `contract_bridge.ttl` (the single source of truth) and
rendered into `_generated_vocab.py` by codegen, CI-diff-enforced (`tests/ontology/test_generated_vocab_in_sync.py`).
This module is the stable import surface the domain builders use -- it RE-EXPORTS the generated constants, so no
code hardcodes the values. To change the taxonomy, edit the ttl and regenerate; never edit the generated file or
re-add literals here. A new domain declares its own entity/edge types in its own pack the same way.
"""
from __future__ import annotations

from rag_wright.packs.contracts.ontology._generated_vocab import (  # generated FROM contract_bridge.ttl (DD-7); do not hardcode here
    AFFILIATE_OF,
    CONTRACTS_WITH,
    ENTITY_TYPES,
    ORGANIZATION,
    PERSON,
    RELATIONSHIP_TYPES,
)

__all__ = ["ORGANIZATION", "PERSON", "CONTRACTS_WITH", "AFFILIATE_OF", "ENTITY_TYPES", "RELATIONSHIP_TYPES"]
