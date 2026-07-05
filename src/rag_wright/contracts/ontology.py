"""The ontology and extraction-target models (FR-C.8, §16.2, ADR-0002).

The ontology is the closed vocabulary the knowledge graph conforms to. T4 owns the ontology
*structure* (the type enums and the fact models that reference them); T8 owns the ontology
*membership* (deriving the concrete types from the Data Catalog, FR-C.8). Both are real: deferring
membership entirely would leave the downstream contract and extraction work with nothing to bind.

- `ClauseCategory`: the 41 CUAD clause categories. Membership here is authoritative (ADR-0002). The
  values are the canonical CUAD label names; T8 reconciles them against the exact label strings in
  the CUAD data once the corpus is acquired (T7).
- `EntityType` and `RelationshipType`: the party/entity types and relationship types. Membership
  here is a minimal, provisional seed grounded in the CUAD + EDGAR corpus; **T8 is the authority**
  that finalizes it from the Data Catalog (FR-C.8). The structure is built so T8 can extend the
  membership without reopening the fact models (see the direction guarantee on `RelationshipFact`).
  Changing the ontology is otherwise an ask-first change (CLAUDE.md boundaries).

The extraction-target models (`EntityNode`, `ClauseFact`, `RelationshipFact`) are what graph
extraction (T5) produces and graph storage (T24) writes. Their type fields are the ontology enums,
so a fact whose type is not in the ontology is rejected at construction (RAC-4). The fact models
extend `GraphFact` (T2), so they carry provenance and a confidence tag; `EntityNode` is a canonical
node (identifier, name, type, no facts), per the thin entity skeleton in SPEC.md section 8.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, field_validator, model_validator

from rag_wright.contracts.identifiers import EntityId
from rag_wright.contracts.provenance import GraphFact


class ClauseCategory(str, Enum):
    """The 41 CUAD clause categories (ADR-0002). Authoritative; T8 reconciles exact label strings."""

    DOCUMENT_NAME = "Document Name"
    PARTIES = "Parties"
    AGREEMENT_DATE = "Agreement Date"
    EFFECTIVE_DATE = "Effective Date"
    EXPIRATION_DATE = "Expiration Date"
    RENEWAL_TERM = "Renewal Term"
    NOTICE_PERIOD_TO_TERMINATE_RENEWAL = "Notice Period To Terminate Renewal"
    GOVERNING_LAW = "Governing Law"
    MOST_FAVORED_NATION = "Most Favored Nation"
    NON_COMPETE = "Non-Compete"
    EXCLUSIVITY = "Exclusivity"
    NO_SOLICIT_OF_CUSTOMERS = "No-Solicit Of Customers"
    COMPETITIVE_RESTRICTION_EXCEPTION = "Competitive Restriction Exception"
    NO_SOLICIT_OF_EMPLOYEES = "No-Solicit Of Employees"
    NON_DISPARAGEMENT = "Non-Disparagement"
    TERMINATION_FOR_CONVENIENCE = "Termination For Convenience"
    ROFR_ROFO_ROFN = "Rofr/Rofo/Rofn"
    CHANGE_OF_CONTROL = "Change Of Control"
    ANTI_ASSIGNMENT = "Anti-Assignment"
    REVENUE_PROFIT_SHARING = "Revenue/Profit Sharing"
    PRICE_RESTRICTIONS = "Price Restrictions"
    MINIMUM_COMMITMENT = "Minimum Commitment"
    VOLUME_RESTRICTION = "Volume Restriction"
    IP_OWNERSHIP_ASSIGNMENT = "IP Ownership Assignment"
    JOINT_IP_OWNERSHIP = "Joint IP Ownership"
    LICENSE_GRANT = "License Grant"
    NON_TRANSFERABLE_LICENSE = "Non-Transferable License"
    AFFILIATE_LICENSE_LICENSOR = "Affiliate License-Licensor"
    AFFILIATE_LICENSE_LICENSEE = "Affiliate License-Licensee"
    UNLIMITED_ALL_YOU_CAN_EAT_LICENSE = "Unlimited/All-You-Can-Eat-License"
    IRREVOCABLE_OR_PERPETUAL_LICENSE = "Irrevocable Or Perpetual License"
    SOURCE_CODE_ESCROW = "Source Code Escrow"
    POST_TERMINATION_SERVICES = "Post-Termination Services"
    AUDIT_RIGHTS = "Audit Rights"
    UNCAPPED_LIABILITY = "Uncapped Liability"
    CAP_ON_LIABILITY = "Cap On Liability"
    LIQUIDATED_DAMAGES = "Liquidated Damages"
    WARRANTY_DURATION = "Warranty Duration"
    INSURANCE = "Insurance"
    COVENANT_NOT_TO_SUE = "Covenant Not To Sue"
    THIRD_PARTY_BENEFICIARY = "Third Party Beneficiary"


class EntityType(str, Enum):
    """Party and entity node types. Provisional seed membership; T8 is the authority (FR-C.8)."""

    ORGANIZATION = "Organization"  # contract parties and EDGAR filers
    PERSON = "Person"  # individual signatories / named individuals


class RelationshipType(str, Enum):
    """Entity-to-entity relationship (edge) types. Provisional seed membership; T8/T10 are the
    authority, finalizing from the EDGAR party-and-entity data. `RelationshipFact` is directed, so
    T8 can add directed corporate-hierarchy types (e.g. a parent/subsidiary edge) without change."""

    CONTRACTS_WITH = "Contracts With"  # co-party to the same agreement (see RelationshipFact)
    AFFILIATE_OF = "Affiliate Of"  # corporate affiliation (parent / subsidiary / affiliate)


class EntityNode(BaseModel):
    """A canonical entity node in the graph skeleton (SPEC.md section 8): identifier, name, type.

    No facts and no confidence: entity nodes are canonical (resolved against the registry, FR-C.7),
    not extracted facts. Conformance is enforced by `entity_type` being an `EntityType`.
    """

    entity_id: EntityId
    entity_type: EntityType
    name: str


class ClauseFact(GraphFact):
    """A clause occurrence extracted from a chunk (extends `GraphFact`: provenance + confidence).

    `category` must be one of the 41 CUAD clause categories, so a non-ontology category is rejected.
    """

    category: ClauseCategory


class RelationshipFact(GraphFact):
    """A directed entity-to-entity relationship extracted from a chunk (extends `GraphFact`:
    provenance + confidence).

    The endpoints are pre-resolution entity mentions (surface forms), directed `source_ref ->
    target_ref`: source and target are distinct roles, not a symmetric pair, so T8 can add directed
    corporate-hierarchy relationship types without reopening this model. Entity resolution
    (FR-C.7 / T24) later maps each ref to a canonical `entity_id`.

    `relationship_type` must be an ontology `RelationshipType`, so a non-ontology relationship is
    rejected. The agreement a `CONTRACTS_WITH` fact derives from is its provenance's source document
    (`provenance.source_doc_id`); because every `GraphFact` requires provenance, that reference is
    always present, which is what makes shared-party multi-hop questions answerable from the graph.

    Self-loop is rejected here only at the ref level (the same mention as both source and target).
    The post-resolution check (two *distinct* mentions that resolve to the same `entity_id`) belongs
    with entity resolution (T24), because two mentions can legitimately resolve to one entity.
    """

    source_ref: str  # pre-resolution entity mention (surface form)
    relationship_type: RelationshipType
    target_ref: str  # pre-resolution entity mention (surface form)

    @field_validator("source_ref", "target_ref")
    @classmethod
    def _ref_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_ref and target_ref must be non-empty entity mentions")
        return v

    @model_validator(mode="after")
    def _no_ref_self_loop(self) -> RelationshipFact:
        if self.source_ref.strip() == self.target_ref.strip():
            raise ValueError(
                "source_ref and target_ref must be distinct mentions (ref-level self-loop); the "
                "post-resolution same-entity_id check belongs with entity resolution (T24)"
            )
        return self
