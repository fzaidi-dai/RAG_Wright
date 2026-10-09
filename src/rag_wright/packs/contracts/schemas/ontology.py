"""The ontology and extraction-target models (FR-C.8, §16.2, ADR-0002).

The ontology is the closed vocabulary the knowledge graph conforms to. T4 owns the ontology
*structure* (the type enums and the fact models that reference them); T8 owns the ontology
*membership* (deriving the concrete types from the Data Catalog, FR-C.8). Both are real: deferring
membership entirely would leave the downstream contract and extraction work with nothing to bind.

- `ClauseCategory`: the 41 CUAD clause categories. Membership here is authoritative (ADR-0002). The
  values are the canonical CUAD label names; T8 reconciles them against the exact label strings in
  the CUAD data once the corpus is acquired (T7).
- Entity/relationship taxonomy (DD-5, ADR-0066/0117): the party/entity node types and the entity-to-entity
  relationship (edge) types are NO LONGER a hardcoded engine enum. `EntityNode.entity_type` and
  `RelationshipFact.relationship_type` are OPAQUE domain strings the caller names; the closed value sets are
  DOMAIN knowledge declared by the pack (the reference contract pack's are in `ontology/contract_taxonomy.py`).
  A new domain supplies its own without reopening these contracts.

The extraction-target models (`EntityNode`, `ClauseFact`, `RelationshipFact`) are what graph
extraction (T5) produces and graph storage (T24) writes. Their type fields are the ontology enums,
so a fact whose type is not in the ontology is rejected at construction (RAC-4). The fact models
extend `GraphFact` (T2), so they carry provenance and a confidence tag; `EntityNode` is a canonical
node (identifier, name, type, no facts), per the thin entity skeleton in SPEC.md section 8.
"""

from __future__ import annotations

from enum import Enum


from rag_wright.pack_sdk import GraphFact
from rag_wright.pack_sdk import EntityNode, RelationshipFact  # noqa: F401 - generic since ING-8b; re-exported


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


class ClauseFact(GraphFact):
    """A clause occurrence extracted from a chunk (extends `GraphFact`: provenance + confidence).

    `category` must be one of the 41 CUAD clause categories, so a non-ontology category is rejected.
    """

    category: ClauseCategory
