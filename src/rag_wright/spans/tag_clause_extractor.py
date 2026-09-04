"""TAGPARSE-INGEST-1b: FUNCTION-INDEPENDENT clause property extraction via client-side tag-parse.

The docling-graph path asks a model to fill the whole 35-field `Clause` template in one server-side-JSON call --
which granite-4.2 flattens/fails (~5/7) on real clauses (ADR-0079). Function-SCOPED extraction was ruled out
because clause-function classification is not accurate enough to gate on (~0.5 top-1; memory
`function-classification-not-load-bearing`). So we decompose the schema WITHOUT the function: the 35 fields are
split into 7 cohesive THEMATIC groups, each a small tag-parse pass over the SAME `Clause` schema (via
`build_tag_structured(..., fields=group)`), run concurrently and merged into one `Clause`. Small focused schemas
are where tag-parse already wins (parties 5/5), and `Clause`'s own validators normalize on the final construct.

`CLAUSE_GROUPS` is domain knowledge (which properties belong together); it is a candidate for ontology migration
(ADR-0066) but lives here for now, like `property_extractor.FUNCTION_DIMENSIONS`. Every non-id `Clause` field is
in exactly one group (a coverage test enforces this).
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from rag_wright.models.tag_structured import build_tag_structured
from rag_wright.ontology.clause_template import Clause

# The 7 thematic groups (5a consents/control + 5b restrictions/duties per the design). document_reference is the
# clause's root id, filled separately (from context / source stem), never asked of the model here.
CLAUSE_GROUPS: dict[str, tuple[str, ...]] = {
    "identity_scope": ("clause_type", "covers", "covers_party_scope", "has_mutuality",
                       "has_asymmetry", "has_favorability"),
    "liability_damages": ("caps", "has_claim_scope", "prohibits_damage", "ld_trigger"),
    "temporal_termination": ("bounded_by", "has_renewal", "has_termination_right", "condition_type"),
    "ip_licensing": ("has_ip_ownership", "has_exclusivity_type", "has_right_of_first_type",
                     "has_mfn_scope", "royalty_basis"),
    "consents_control": ("has_assignment_consent", "has_coc_consent", "has_escrow_release_trigger"),
    "governing_law_dispute": ("governed_by", "dispute_method"),  # split out of the grab-bag: a focused pass so
                                                                 # jurisdiction isn't buried among 8 other fields
    "restrictions_duties": ("requires_duty", "has_restriction_scope", "prohibits_solicit", "has_warranty_scope",
                            "audit_frequency", "commitment_quantum", "collateral_type"),
    "exceptions": ("excepts", "confidentiality_exception", "force_majeure_event"),
}

_GROUP_PROMPT = (
    "You are extracting the typed properties of ONE contract clause. Extract ONLY the properties below that are "
    "EXPLICITLY stated in the clause; omit any that are not present -- do NOT guess or invent a value. Read each "
    "value verbatim from the clause.\n\nCLAUSE:\n{text}"
)

# The aspect gate: a coarse "which of these aspects does the clause touch?" pass that prunes clearly-irrelevant
# groups (cost + noise). RECALL-BIASED (include-if-plausible) so it is NOT a load-bearing exclusion gate -- a
# false include is cheap (an empty group pass) and the grounding judge drops any stray value; a false exclude is
# the only harm, so the prompt errs toward inclusion. `identity_scope` is cross-cutting and ALWAYS run.
_ALWAYS = "identity_scope"
_ASPECT_DESC: dict[str, str] = {
    "identity_scope": "the clause's core nature: its type, what/who it covers, mutuality, favorability",
    "liability_damages": "caps on liability, damages waivers/exclusions, liquidated damages, claim scope",
    "temporal_termination": "time limits/term/duration, renewal, termination rights, conditions/triggers",
    "ip_licensing": "IP ownership, license grants, exclusivity, right-of-first, MFN, royalties",
    "consents_control": "consent/notice for assignment or change-of-control, escrow release triggers",
    "governing_law_dispute": "governing law / jurisdiction, and how disputes are resolved (litigation, "
                             "arbitration, mediation)",
    "restrictions_duties": "procedural duties, restrictions, non-solicit, warranty scope, audit rights, "
                           "minimum commitments, collateral",
    "exceptions": "carve-outs / exceptions to obligations (e.g. confidentiality or force-majeure exceptions)",
}


class _AspectGate(BaseModel):
    aspects: list[str] = []


def _aspect_prompt(text: str) -> str:
    catalog = "\n".join(f"- {k}: {v}" for k, v in _ASPECT_DESC.items() if k != _ALWAYS)
    return ("Which of these ASPECTS does the following contract clause touch on? List EVERY aspect that plausibly "
            "applies -- when unsure, INCLUDE it (over-including is fine, missing one is not).\n\n"
            f"Aspects:\n{catalog}\n\nCLAUSE:\n{text}")


async def aselect_aspects(text: str, model_id: str, *, temperature: float = 0.0) -> set[str]:
    """Coarse recall-biased aspect gate -> the group keys to extract (always incl. `identity_scope`). On any
    failure, degrade to ALL groups (never silently narrow)."""
    valid = set(CLAUSE_GROUPS)
    try:
        gate = await build_tag_structured(
            model_id, _AspectGate, temperature=temperature, label="aspect-gate").ainvoke(_aspect_prompt(text))
        chosen = {a.strip() for a in gate.aspects} & valid
        return chosen | {_ALWAYS} if chosen else valid  # empty/garbled -> don't narrow
    except Exception:  # noqa: BLE001 - gate failure must never DROP groups; fall back to extracting all
        return valid


import re as _re

_MAX_VALUE_CHARS = 240  # a clause PROPERTY value is short ("12_months", "Delaware"); longer = leaked prose
_TAG_LIKE = _re.compile(r"<[A-Za-z_][\w-]*>")  # a value must never contain XML tags (model reasoning/prompt leak)


def _sane(value: Any) -> Any:
    """Drop a garbage string value (a leaked chain-of-thought / prompt echo): too long, or containing XML tags.
    Non-string values pass through (enums/sub-models/lists can't carry this leak). A dropped value -> None so the
    field falls back to its default (never store reasoning text as a clause property)."""
    if isinstance(value, str) and (len(value) > _MAX_VALUE_CHARS or _TAG_LIKE.search(value)):
        return None
    return value


def _merge_group_results(results: list[Clause | None], doc_ref: str) -> Clause:
    """Combine the per-group pass results into one `Clause`: take each group's OWN fields from its pass (a failed
    pass -> that group defaults), sanitize string values (drop leaked prose), then construct `Clause` once so its
    validators normalize everything."""
    merged: dict[str, Any] = {}
    for (group, fields), res in zip(CLAUSE_GROUPS.items(), results):
        if res is None:
            continue
        for f in fields:
            v = _sane(getattr(res, f))
            if v is not None:
                merged[f] = v
    merged["document_reference"] = doc_ref or None
    return Clause(**merged)


async def atag_extract_clause(text: str, model_id: str, *, document_reference: str = "",
                              temperature: float = 0.0, gate: bool = True) -> Clause:
    """Extract a clause's typed properties as ONE `Clause`, function-independently: thematic tag-parse passes over
    the `Clause` schema, merged. When `gate` is set (default), a coarse recall-biased aspect gate first prunes
    clearly-irrelevant groups (cost + noise); a skipped group defaults. Each pass degrades on its own
    (build_tag_structured re-asks then omits-to-default); a pass that still fails leaves its group at defaults
    (never fails the whole clause). `gate=False` runs every group (for the A/B baseline)."""
    active = await aselect_aspects(text, model_id, temperature=temperature) if gate else set(CLAUSE_GROUPS)

    async def _pass(group: str, fields: tuple[str, ...]) -> Clause | None:
        if group not in active:
            return None
        try:
            return await build_tag_structured(
                model_id, Clause, fields=set(fields), temperature=temperature, label="clause-group",
            ).ainvoke(_GROUP_PROMPT.format(text=text))
        except Exception:  # noqa: BLE001 - a persistently-failing group degrades to defaults, not a hard error
            return None

    results = await asyncio.gather(*[_pass(g, fields) for g, fields in CLAUSE_GROUPS.items()])
    return _merge_group_results(list(results), document_reference)


def tag_extract_clause(text: str, model_id: str, *, document_reference: str = "",
                       temperature: float = 0.0, gate: bool = True) -> Clause:
    """Sync wrapper over `atag_extract_clause` (parity with the docling-graph `extract_clause`)."""
    return asyncio.run(atag_extract_clause(
        text, model_id, document_reference=document_reference, temperature=temperature, gate=gate))
