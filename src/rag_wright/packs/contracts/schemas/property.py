"""The clause PROPERTY schema and contract (T57, FR-C.6, ADR-0025).

Demand-derived from the 57 ACORD test queries (schema review gate, approved). Every ACORD query is a
FUNCTION (clause type, `schemas/function.py`) plus zero-or-more PROPERTY constraints; this module is
the contract for the property layer the extractor (T57b) populates and the property graph (T57c)
persists. The clause itself stays source-of-truth in the clause OKF bundle; the property graph points
back to it (`clause_id`), and the schema stores no clause text.

Two tiers, mirroring the approved design:
- cross-cutting dimensions that recur across the liability/indemnity family (mutuality, favorability,
  carve_out, covered_subject, covered_parties, party_asymmetry);
- function-specific dimensions (cap basis/quantum, damage type, warranty scope, claim scope,
  procedural right, governing-law multiplicity, IP ownership, non-solicit target, renewal mechanism,
  notice period, jurisdiction).

Each PROPERTY is a graph fact: `PropertyAssertion` extends `GraphFact` (FR-S.4 provenance +
EXTRACTED/INFERRED/AMBIGUOUS confidence) and cites the operative span it was read from (`span_id`,
the ADR-0025 join key) -- no claim without a citation (FR-Q.6). Closed-vocabulary dimensions validate
their value against `CLOSED_VOCAB`; a value outside the vocabulary is admissible ONLY as an AMBIGUOUS
assertion (the `other` escape, approved), so the extractor and the T58 query-decomposer share exactly
one vocabulary. Multi-valued dimensions (a carve-out set) are several assertions of the same
dimension; scalar dimensions are at most one -- the graph writer turns each assertion into one
typed edge to a (deduped) value node.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, field_validator, model_validator

from rag_wright.packs.contracts.schemas.function import FUNCTION_LABEL_SET, NO_FUNCTION, FunctionScore
from rag_wright.packs.contracts.schemas.ontology import ClauseCategory
from rag_wright.contracts.provenance import ConfidenceTag, GraphFact
from rag_wright.packs.contracts.ontology._generated_vocab import VOCAB as _GENERATED_VOCAB  # ADR-0066: generated FROM the ttl


class PropertyDimension(str, Enum):
    """The property axes the ACORD queries filter on. Tier 1 = cross-cutting; tier 2 = function-specific."""

    # tier 1 -- cross-cutting (recur across the liability / indemnity family)
    MUTUALITY = "mutuality"
    FAVORABILITY = "favorability"
    CARVE_OUT = "carve_out"
    COVERED_SUBJECT = "covered_subject"
    COVERED_PARTIES = "covered_parties"
    PARTY_ASYMMETRY = "party_asymmetry"
    # tier 2 -- function-specific
    CAP_BASIS = "cap_basis"
    CAP_QUANTUM = "cap_quantum"  # open-valued (e.g. "12_months", "1x_fees")
    DAMAGE_TYPE = "damage_type"
    WARRANTY_SCOPE = "warranty_scope"
    CLAIM_SCOPE = "claim_scope"
    PROCEDURAL = "procedural"
    JURISDICTION = "jurisdiction"  # open-valued (e.g. "england", "new_york")
    LAW_MULTIPLICITY = "law_multiplicity"
    IP_OWNERSHIP = "ip_ownership"
    NONSOLICIT_TARGET = "nonsolicit_target"
    TEMPORAL_BOUND = "temporal_bound"  # open-valued (e.g. "12_months", "unbounded")
    RENEWAL_MECHANISM = "renewal_mechanism"
    NOTICE_PERIOD = "notice_period"  # open-valued
    # tier 3 -- CUAD-family extensions (KG-4: full CUAD clause coverage beyond the ACORD-derived set)
    EXCLUSIVITY_TYPE = "exclusivity_type"
    RIGHT_OF_FIRST_TYPE = "right_of_first_type"
    RESTRICTION_SCOPE = "restriction_scope"  # non-compete scope
    COC_CONSENT = "coc_consent"  # change-of-control consent regime
    ASSIGNMENT_CONSENT = "assignment_consent"  # anti-assignment consent regime
    ESCROW_RELEASE_TRIGGER = "escrow_release_trigger"  # source-code escrow
    MFN_SCOPE = "mfn_scope"
    TERMINATION_RIGHT = "termination_right"  # termination-for-convenience
    AUDIT_FREQUENCY = "audit_frequency"  # open-valued (e.g. "annual", "quarterly")
    COMMITMENT_QUANTUM = "commitment_quantum"  # open-valued (minimum commitment / volume restriction)
    LD_TRIGGER = "ld_trigger"  # open-valued (liquidated-damages trigger)
    # ADR-0049 (2): new closed-vocab dimensions for the taxonomy-gap clause types (the type-specific facet each
    # one carries that had no existing dimension). Vocab domain-designed + corpus-checked (ADR-0049 step 2).
    DISPUTE_METHOD = "dispute_method"    # how disputes are resolved (Dispute Resolution)
    COLLATERAL_TYPE = "collateral_type"  # collateral a security interest attaches to (Security Interest; list)
    FORCE_MAJEURE_EVENT = "force_majeure_event"              # excused events (Force Majeure; list)
    ROYALTY_BASIS = "royalty_basis"                          # how a royalty is calculated (Royalties)
    CONFIDENTIALITY_EXCEPTION = "confidentiality_exception"  # permitted disclosures (Confidentiality; list)
    CONDITION_TYPE = "condition_type"                        # kind of condition (Condition Precedent)


# Closed controlled vocabularies (approved OQ3). A dimension NOT in this map is open-valued
# (jurisdiction, cap_quantum, temporal_bound, notice_period) -- any non-empty value with an
# EXTRACTED/INFERRED confidence is admissible. `cap_basis` keeps a closed enum (the shape of the cap)
# while `cap_quantum` carries the light open scalar (no structured money object -- SPEC section 8).
# ADR-0066: the closed vocabularies are GENERATED FROM contract_bridge.ttl (the source of truth) into
# _generated_vocab.VOCAB (string-keyed); here they are re-keyed by PropertyDimension. To change a vocabulary,
# edit the ttl and re-run scripts/generate_contract_python.py -- never edit the value sets in Python.
CLOSED_VOCAB: dict[PropertyDimension, frozenset[str]] = {
    PropertyDimension(dim): values for dim, values in _GENERATED_VOCAB.items()
}

_FOLIO_BASE = "https://folio.openlegalstandard.org/"

# Clause-TYPE -> FOLIO IRI (naming alignment only, no OWL import; verified against the live FOLIO API,
# T57). 15/16 aligned types have a home; Joint IP Ownership and Revenue/Profit Sharing have no clean
# FOLIO class (native, no IRI). Keyed by the function label string (CUAD value or extension value).
FOLIO_CLAUSE_IRI: dict[str, str] = {
    ClauseCategory.CAP_ON_LIABILITY.value: _FOLIO_BASE + "RD0R9lAU0GYr2Rm3CDcMWQn",
    ClauseCategory.GOVERNING_LAW.value: _FOLIO_BASE + "RCinm0jvGGkzcHth7AnasRI",
    ClauseCategory.LIQUIDATED_DAMAGES.value: _FOLIO_BASE + "R8gVw3PYPaZJ9kce3wE60ag",
    ClauseCategory.CHANGE_OF_CONTROL.value: _FOLIO_BASE + "Rx73OtOSnOdjzb248cqESZ",
    ClauseCategory.AUDIT_RIGHTS.value: _FOLIO_BASE + "Rbjf6IGvMubNB3VG6OHa2J",
    ClauseCategory.NO_SOLICIT_OF_EMPLOYEES.value: _FOLIO_BASE + "RBPNQSqdDfSS0uPPJ8pfxVL",
    ClauseCategory.NO_SOLICIT_OF_CUSTOMERS.value: _FOLIO_BASE + "RBPNQSqdDfSS0uPPJ8pfxVL",
    ClauseCategory.ROFR_ROFO_ROFN.value: _FOLIO_BASE + "R8vrLOm6RKTfx8fw40CYWsh",
    ClauseCategory.THIRD_PARTY_BENEFICIARY.value: _FOLIO_BASE + "R97DdQGgeUgH9OJAvTnJreN",
    ClauseCategory.IP_OWNERSHIP_ASSIGNMENT.value: _FOLIO_BASE + "RCvIzbBC4HsPoR3TCjrDPSr",
    ClauseCategory.MINIMUM_COMMITMENT.value: _FOLIO_BASE + "RClWiJIjOouUllydauOfq00",
    ClauseCategory.RENEWAL_TERM.value: _FOLIO_BASE + "R6ZPNSiwrrkYAEVTRoOKiv",
    "Indemnification": _FOLIO_BASE + "R9oz08cWJcI23x0nYU1h0it",
    "Indirect/Consequential Damages Waiver": _FOLIO_BASE + "RBpLvGtycyCm93U686txQg2",
    "Warranty Disclaimer": _FOLIO_BASE + "RC8mge0bMEuSUAMJUlgN0rZ",
}

# Carve-out / covered SUBJECT value -> FOLIO IRI. Only these four subjects have a standalone FOLIO
# concept IRI (T57); the rest are native (no IRI). Used to tag shared `Exception`/`Subject` value nodes.
FOLIO_SUBJECT_IRI: dict[str, str] = {
    "fraud": _FOLIO_BASE + "RqGxSnAp9vX42GRKHqwvBe",
    "gross_negligence": _FOLIO_BASE + "RB2XGaLZqJPXLJOm052Pwrf",
    "willful_misconduct": _FOLIO_BASE + "RCuhDmyUHjn92exJ8dx1zO1",
    "confidentiality": _FOLIO_BASE + "ROqkYuzXx4hg7XafuJWBfJ",
}


class PropertyAssertion(GraphFact):
    """One property of a clause: a (dimension, value) read from an operative span, carrying provenance
    + confidence (FR-S.4) and the span citation (FR-Q.6, ADR-0025). A closed-vocabulary value outside
    its vocabulary is admissible ONLY as AMBIGUOUS (the `other` escape) -- this keeps the extractor and
    the query-decomposer on one shared vocabulary while still recording genuinely novel values."""

    dimension: PropertyDimension
    value: str
    span_id: str = ""  # the operative span cited (ADR-0025 join key); "" = clause-level only

    @field_validator("value")
    @classmethod
    def _value_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("property value must be non-empty")
        return v

    @model_validator(mode="after")
    def _value_in_vocab_or_ambiguous(self) -> PropertyAssertion:
        vocab = CLOSED_VOCAB.get(self.dimension)
        if vocab is not None and self.value not in vocab and self.confidence != ConfidenceTag.AMBIGUOUS:
            raise ValueError(
                f"value {self.value!r} is not in the closed vocabulary for {self.dimension.value} "
                f"({sorted(vocab)}); an out-of-vocabulary value is admissible only as an AMBIGUOUS "
                "assertion (the 'other' escape)"
            )
        return self


class ClausePropertyRecord(BaseModel):
    """The property layer for one clause: its FUNCTION plus its property assertions.

    `clause_id` is the parent chunk id's string form (the clause is source-of-truth in the clause OKF
    bundle; the property graph points back to it). `function` must be a member of the retrieval
    function taxonomy (FUNCTION_LABELS). Every assertion is anchored to this clause: its provenance's
    chunk id must be this `clause_id`, so a property cannot cite a different clause (FR-Q.6). `folio_iri`
    names the clause TYPE (naming alignment only) and is filled from `FOLIO_CLAUSE_IRI` when known.
    """

    clause_id: str
    function: str
    folio_iri: str = ""
    # The operative span this clause was extracted from (1:1; ADR-0025). Known at extraction (op.span_id) and
    # persisted here so a PROPERTY-LESS clause still has a reliable, one-to-one span link for citation/rehydration
    # -- not lost, and never guessed by function label (which is one-to-many). "" only for legacy pre-backfill rows.
    span_id: str = ""
    assertions: list[PropertyAssertion] = []
    # INGEST-LLM-CLASSIFIER (ADR-0048): the multi-label classification, ranked primary-first. `function` above is
    # the PRIMARY (functions[0].function) -- the label query readers use; this additive list carries the
    # secondaries + confidence for the deferred multi-label consumers. Empty on legacy / LegalBERT-single records.
    functions: list[FunctionScore] = []

    @field_validator("function")
    @classmethod
    def _function_in_taxonomy(cls, v: str) -> str:
        # a real clause's function is a taxonomy member; the `NO_FUNCTION` sentinel is allowed ONLY for a
        # query-constraint record (a query has no clause function -- only its extracted properties are used).
        if v != NO_FUNCTION and v not in FUNCTION_LABEL_SET:
            raise ValueError(
                f"function {v!r} is not in the retrieval function taxonomy (FUNCTION_LABELS) "
                f"or the {NO_FUNCTION!r} no-function sentinel"
            )
        return v

    @model_validator(mode="after")
    def _assertions_anchored_to_clause(self) -> ClausePropertyRecord:
        for a in self.assertions:
            if str(a.provenance.chunk_id) != self.clause_id:
                raise ValueError(
                    "every assertion must be anchored to the record's clause_id "
                    f"(assertion provenance chunk_id {str(a.provenance.chunk_id)!r} != "
                    f"clause_id {self.clause_id!r})"
                )
        return self
