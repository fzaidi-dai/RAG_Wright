"""The retrieval FUNCTION taxonomy (T57, FR-C.6, ADR-0025).

The set of clause types the FUNCTION classifier (T56) routes on. It is a SUPERSET of the 41 CUAD
`ClauseCategory` (mirrored by value, so there is no drift) plus the three ACORD query families CUAD
has no class for: Indemnification (14/57 test queries), the indirect/consequential damages waiver,
and the warranty disclaimer (the bulk of the "Limitation of Liability" family beyond Cap/Uncapped).

This is kept DISTINCT from `ClauseCategory` on purpose. `ClauseCategory` is the graph EXTRACTION
ontology (ADR-0002): a closed vocabulary the knowledge graph conforms to, not reopened here. The
FUNCTION taxonomy is a RETRIEVAL concern (which clause type a span is routed under). The two serve
different layers even though 41 labels coincide by value, so extending function routing does not
reopen the extraction ontology.

`FUNCTION_LABELS` is the classifier's full label space and its retrain target (the T56 LegalBERT is
retrained over these classes plus its own NONE sentinel; NONE is not a function type and is not listed
here). ADR-0048 step 2 grew it 44 -> 52 with 8 taxonomy-gap functions the full-corpus classifier
surfaced (curated LLM-assisted with human oversight), plus a curated FOLD alias map (`_FUNCTION_ALIASES`)
that resolves recurring synonyms to their canonical label.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, field_validator

from rag_wright.contracts.ontology import ClauseCategory


class FunctionClassification(BaseModel):
    """CAP-REG-2: the ranked FUNCTION_LABELS a span or query is classified into (most relevant first).
    The shared output contract of the LegalBERT `clause_function_classification` (model) and the
    taxonomy-constrained `query_function_classification` (agent_skill)."""

    labels: list[str]


class ExtendedFunction(str, Enum):
    """The ACORD query families the 41 CUAD `ClauseCategory` has no class for (T57). Values are the
    canonical function labels the classifier emits and the query-decomposer targets."""

    INDEMNIFICATION = "Indemnification"
    INDIRECT_DAMAGES_WAIVER = "Indirect/Consequential Damages Waiver"
    WARRANTY_DISCLAIMER = "Warranty Disclaimer"


class TaxonomyGapFunction(str, Enum):
    """INGEST-LLM-CLASSIFIER step 2 (ADR-0048): 8 clause functions the full-corpus LLM classifier surfaced as
    recurring OTHER (out-of-taxonomy) clause types across the unified CUAD+ACORD corpus, then curated LLM-assisted
    with human oversight (`data/eval/taxonomy_gaps/curation_proposal.json`, my recommended 8-ADD delta approved).
    Genuinely distinct from the 44 CUAD/ACORD labels: CUAD has no generic Confidentiality, Royalties, Payment
    Terms, Force Majeure, Dispute Resolution, Record Retention, Security Interest, or Condition Precedent class."""

    CONFIDENTIALITY = "Confidentiality"
    ROYALTIES = "Royalties"
    PAYMENT_TERMS = "Payment Terms"
    DISPUTE_RESOLUTION = "Dispute Resolution"
    RECORD_RETENTION = "Record Retention"
    SECURITY_INTEREST = "Security Interest"
    CONDITION_PRECEDENT = "Condition Precedent"
    FORCE_MAJEURE = "Force Majeure"


# The classifier's full label space: the 41 CUAD categories (by value, no drift), then the 3 ACORD
# extensions, then the 8 ADR-0048 taxonomy-gap additions. Order is stable (CUAD, extension, gap) so a
# retrain's label<->id map is reproducible. NONE is the off-taxonomy sentinel, not a function -> not listed.
FUNCTION_LABELS: tuple[str, ...] = tuple(
    [c.value for c in ClauseCategory]
    + [f.value for f in ExtendedFunction]
    + [f.value for f in TaxonomyGapFunction]
)
FUNCTION_LABEL_SET: frozenset[str] = frozenset(FUNCTION_LABELS)

# The no-clause-function sentinel (the classifier's off-taxonomy NONE). NOT a function type, so NOT in
# FUNCTION_LABELS. A `ClausePropertyRecord` carries it ONLY for a QUERY-constraint record (a query has no clause
# function -- only its extracted properties matter); a real ingested clause never uses it (the ingest extracts
# clauses only for canonical functions).
NO_FUNCTION: str = "NONE"

# The classifier was trained on CUAD's label strings, which differ in CASE from the canonical taxonomy for
# a few labels (e.g. CUAD "Ip Ownership Assignment" vs the canonical "IP Ownership Assignment"). Normalize
# the classifier output to the canonical label at the boundary (memory: normalize at the boundary), keyed
# case-insensitively.
_FUNCTION_BY_CASEFOLD: dict[str, str] = {label.casefold(): label for label in FUNCTION_LABELS}

# ADR-0048 step 2: the curated FOLD map -- recurring synonyms/spelling/variant clause types the full-corpus
# classifier surfaced, each mapped to its canonical `FUNCTION_LABELS` entry (LLM-proposed, human-reconciled:
# the 3 LLM mis-folds were dropped, the royalty family retargeted to the new Royalties label, and RoFR +
# Milestone Payment folded rather than dropped). Authored in code (no external map to drift), keyed by
# canonical target for readability; inverted + casefolded into `_FUNCTION_ALIAS_BY_CASEFOLD` below.
_FUNCTION_ALIASES: dict[str, tuple[str, ...]] = {
    "Cap On Liability": ("Limitation of Liability", "Limitations of Liability", "Liability Limitation"),
    "Anti-Assignment": ("Assignment", "Assignment of Capacity", "Non-Assignment"),
    "Termination For Convenience": (
        "Termination", "Termination For Cause", "Effect of Termination", "Termination Clause",
        "Cancellation", "Termination Effects", "Rights and Obligations Upon Termination"),
    "Expiration Date": ("Term",),
    "Insurance": ("Insurance Requirement", "Insurance Type", "Insurance Coverage Details"),
    "No-Solicit Of Employees": ("Non-Solicit Of Employees",),
    "No-Solicit Of Customers": ("Non-Solicit Of Customers",),
    "Warranty Duration": ("Warranty Grant", "Product Warranty", "Warranty"),
    "Warranty Disclaimer": (
        "Disclaimer of Warranty", "Disclaimer of Representations and Warranties", "Disclaimer",
        "Liability Disclaimer"),
    "Liquidated Damages": ("Penalty", "Make-Whole Payment"),
    "Indemnification": ("Intellectual Property Indemnification", "Indemnity and Limitation of Liability"),
    "License Grant": (
        "Content License Restrictions", "Use Restrictions", "License Grant Restrictions", "Restriction Of Use"),
    "Notice Period To Terminate Renewal": ("Notice Period To Terminate",),
    "Non-Compete": ("Non-Compete Exception",),
    "Governing Law": ("Jurisdiction",),
    "Audit Rights": ("Inspection", "Inspection Rights"),
    "IP Ownership Assignment": (
        "Ownership", "Domain Name Assignment", "Intellectual Property Rights", "Patents",
        "Intellectual Property Assignment"),
    "Revenue/Profit Sharing": ("Revenue Sharing", "Payment/Revenue Sharing"),
    "Competitive Restriction Exception": (
        "Definition of Class C Breaches", "Other Restriction Exception", "Other Restriction"),
    "Rofr/Rofo/Rofn": ("Right of First Refusal",),
    # the royalty family consolidates into the new Royalties label; Milestone Payment -> the new Payment Terms
    "Royalties": ("Royalty Grant", "Royalty", "Royalty Obligation", "Royalty Payment"),
    "Payment Terms": ("Milestone Payment",),
}
_FUNCTION_ALIAS_BY_CASEFOLD: dict[str, str] = {
    alias.casefold(): canon for canon, aliases in _FUNCTION_ALIASES.items() for alias in aliases
}


def canonical_function(label: str) -> str | None:
    """Map a function label to its canonical `FUNCTION_LABELS` entry, or None if it matches none. Case-
    insensitive, and resolves the ADR-0048 curated FOLD aliases (e.g. 'Limitation of Liability' -> 'Cap On
    Liability', 'Royalty Grant' -> 'Royalties'). Exact/cased match wins over an alias."""
    key = label.strip().casefold()
    return _FUNCTION_BY_CASEFOLD.get(key) or _FUNCTION_ALIAS_BY_CASEFOLD.get(key)


class FunctionConfidence(str, Enum):
    """INGEST-LLM-CLASSIFIER (ADR-0048): the LLM clause classifier's coarse confidence in a function assignment.
    Ordinal, not a float -- LLMs are not calibrated on numeric self-confidence; a floor (>= medium) filters weak
    labels, so a clause with one clear function stays single while a genuinely mixed clause keeps 2-3."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FunctionScore(BaseModel):
    """INGEST-LLM-CLASSIFIER (ADR-0048): one function a clause is classified into, with coarse confidence. In a
    ranked list the first is the PRIMARY (the label kept on `Clause.function` for the query legs). `function` is
    normalized to its canonical `FUNCTION_LABELS` entry at the boundary; a non-canonical label (incl. the NONE
    sentinel) is rejected (strict contract; normalize upstream)."""

    function: str
    confidence: FunctionConfidence

    @field_validator("function")
    @classmethod
    def _canonicalize(cls, v: str) -> str:
        canon = canonical_function(v)
        if canon is None:
            raise ValueError(f"function must be a canonical FUNCTION_LABELS label, got {v!r}")
        return canon


def primary_function(scores: list[FunctionScore]) -> str | None:
    """The PRIMARY (highest-ranked) function of a ranked `FunctionScore` list (primary first), or None if empty."""
    return scores[0].function if scores else None
