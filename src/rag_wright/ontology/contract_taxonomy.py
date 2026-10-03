"""The reference CONTRACT domain pack's entity-node + entity-relationship taxonomy (DD-5, ADR-0066/0117).

De-domaining (DD-5): the engine's generic primitives + contracts are taxonomy-free -- `entity_type` and
`relationship_type` are plain strings the CALLER (a domain graph) names. The closed value sets below are DOMAIN
knowledge, so they live in the reference pack, not in the engine contracts. A new domain supplies its own.

(The fully ADR-0066-faithful end state declares these in `contract_bridge.ttl` and renders them like the other
closed vocabularies; relocating them into the ttl/codegen path is the separately-tracked ADR-0066 follow-up. Until
then this module is the single reference-pack home -- never re-add them as a hardcoded enum in the engine contracts.)
"""
from __future__ import annotations

# Entity-node types (was contracts.ontology.EntityType).
ORGANIZATION = "Organization"  # contract parties and corporate filers
PERSON = "Person"  # individual signatories / named individuals

# Entity-to-entity relationship (edge) types (was contracts.ontology.RelationshipType).
CONTRACTS_WITH = "Contracts With"  # co-party to the same agreement
AFFILIATE_OF = "Affiliate Of"  # corporate affiliation (parent / subsidiary / affiliate)

ENTITY_TYPES = frozenset({ORGANIZATION, PERSON})
RELATIONSHIP_TYPES = frozenset({CONTRACTS_WITH, AFFILIATE_OF})
