"""P2: the KG structural-feature schema (v2) + a compact feature string appended to the cross-encoder input.
Same schema validated in the feature-separation experiments (mutuality/reciprocity/disclaimer-breadth/
indemnity-bearer separate ACORD's favorability grades). Extraction is a per-clause ingestion step (once).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Side(str, Enum):
    seller = "seller_side"
    buyer = "buyer_side"
    both = "both"
    third_party = "third_party"
    unknown = "unknown"


class Mutuality(str, Enum):
    one_sided = "one_sided"
    mutual = "mutual"
    unknown = "unknown"


class Reciprocity(str, Enum):
    one_sided = "one_sided"
    mutual_symmetric = "mutual_symmetric"
    mutual_asymmetric = "mutual_asymmetric"
    unknown = "unknown"


class DisclaimerBreadth(str, Enum):
    broad = "broad"
    narrow_preserves_express = "narrow_preserves_express"
    not_a_disclaimer = "not_a_disclaimer"


class IndemnityBearer(str, Enum):
    seller_side = "seller_side"
    buyer_side = "buyer_side"
    both = "both"
    not_indemnity = "not_indemnity"


class CapKind(str, Enum):
    monetary_amount = "monetary_amount"
    fees_paid = "fees_paid"
    other_formula = "other_formula"
    excludes_damage_types_only = "excludes_damage_types_only"
    uncapped = "uncapped"
    none = "none"


class RelationalClauseV2(BaseModel):
    clause_type: str = Field(description="short type label, e.g. 'liability cap', 'warranty disclaimer', 'indemnification'")
    limits_liability_of: Side = Field(description="whose liability this clause caps/limits/disclaims (seller_side/buyer_side/both)")
    liability_runs_to: Side = Field(description="the party protected against / to whom liability would otherwise be owed")
    cap_kind: CapKind
    cap_quantum: str = Field(default="", description="the cap amount/formula verbatim if any, else ''")
    excluded_damages: list[str] = Field(default_factory=list, description="damage types excluded")
    carve_outs: list[str] = Field(default_factory=list, description="exceptions to the limitation/disclaimer")
    mutuality: Mutuality = Field(description="one_sided if only one party's obligation is limited; mutual if both")
    reciprocity: Reciprocity = Field(description="one_sided; mutual_symmetric; or mutual_asymmetric (both bear it but scope favors one side)")
    disclaimer_breadth: DisclaimerBreadth = Field(description="warranty disclaimers: broad blanket vs narrow_preserves_express; else not_a_disclaimer")
    indemnity_obligation_on: IndemnityBearer = Field(description="indemnities: which side must indemnify (seller_side/buyer_side/both) or not_indemnity")


PROMPT = ("You are a senior contract attorney. Extract the STRUCTURAL FEATURES of this clause into the schema, "
          "grounded in the text. Be precise about reciprocity (one_sided vs mutual, and if mutual whether the "
          "scope is symmetric or asymmetric), disclaimer breadth (broad blanket vs narrow/preserves express "
          "warranties), and, for indemnities, which side must indemnify.\n\nClause:\n{clause}")


def build_feat_string(rec: dict) -> str:
    """Compact, tokenizer-friendly rendering of the discriminative structural features."""
    r = RelationalClauseV2(**rec) if not isinstance(rec, RelationalClauseV2) else rec
    return (f"type={r.clause_type}; mutuality={r.mutuality.value}; reciprocity={r.reciprocity.value}; "
            f"limits={r.limits_liability_of.value}; runs_to={r.liability_runs_to.value}; "
            f"disclaimer={r.disclaimer_breadth.value}; indemnity_on={r.indemnity_obligation_on.value}; "
            f"cap={r.cap_kind.value}; carveouts={len(r.carve_outs)}; excluded_damages={len(r.excluded_damages)}")
