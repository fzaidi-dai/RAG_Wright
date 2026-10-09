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
import os
from typing import Any

from rag_wright.pack_sdk import DEFAULT_GENERAL
from rag_wright.pack_sdk import build_tag_structured, field_kind
from rag_wright.packs.contracts.ontology.clause_template import Clause

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

# NOTE (issue 0036): TWO cost-cutting ideas were tried here and BOTH measured a ~15-18% property-recall loss on
# Qwen (the current default), concentrated in `excepts` (carve-outs) -- so neither was adopted. (1) A coarse
# "aspect gate" that pruned groups per clause (removed entirely). (2) Batching two clauses per group call (recall
# 0.818 at samples=1, 0.844 at samples=4 -- still a real regression, not variance). Splitting the model's attention
# across clauses, or pruning groups, both under-extract the hard cross-cutting fields. So every group always runs,
# one clause per call; the only recall-preserving cost lever is `is_extractable_span` (fewer spans reach here).


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


def _uninformative(v: Any) -> bool:
    """A scalar value carrying no information: None/empty, or an enum OTHER escape (dropped downstream anyway)."""
    if v is None:
        return True
    s = str(getattr(v, "value", v)).strip()
    return s == "" or s in ("Other", "OTHER") or "Unknown" in s


def _item_key(item: Any) -> Any:
    """A hashable identity for a list item so the union can dedup: an enum by its value, a sub-model by its dump."""
    if hasattr(item, "model_dump"):
        return tuple(sorted((k, str(v)) for k, v in item.model_dump().items()))
    return getattr(item, "value", item)


def _union_lists(lists: list[Any]) -> list[Any]:
    """Order-preserving UNION of a list-valued field across samples -- the fix for granite's list under-enumeration
    (each sample may emit a different subset; the union recovers the full set)."""
    out: list[Any] = []
    seen: set[Any] = set()
    for lst in lists:
        for item in (lst or []):
            k = _item_key(item)
            if k not in seen:
                seen.add(k)
                out.append(item)
    return out


def _combine_group(samples: list[Clause | None], fields: tuple[str, ...]) -> dict[str, Any]:
    """Combine a group's N sampled passes into one field dict: LIST-valued fields are UNIONed across samples
    (list-completeness), scalar/nested fields take the first informative (non-OTHER) sane value. A single sample
    (N=1) reduces to the prior behavior."""
    valid = [r for r in samples if r is not None]
    out: dict[str, Any] = {}
    for f in fields:
        kind, _sub, _hint = field_kind(Clause.model_fields[f].annotation)
        vals = [getattr(r, f) for r in valid]
        if kind in ("list_scalar", "nested_list"):
            merged = _union_lists(vals)
            if merged:
                out[f] = merged
        else:
            sane = [_sane(v) for v in vals]
            pick = next((v for v in sane if v is not None and not _uninformative(v)),
                        next((v for v in sane if v is not None), None))
            if pick is not None:
                out[f] = pick
    return out


def _group_has_list(fields: tuple[str, ...]) -> bool:
    """True if the group has any LIST-valued field (list_scalar / nested_list) -- the fields where under-
    enumeration bites, and the only ones a cross-model union is worth paying a second model for."""
    return any(field_kind(Clause.model_fields[f].annotation)[0] in ("list_scalar", "nested_list") for f in fields)


async def atag_extract_clause(text: str, model_id: str, *, document_reference: str = "",
                              temperature: float = 0.0, samples: int | None = None,
                              list_model: str | None = None) -> Clause:
    """Extract a clause's typed properties as ONE `Clause`, function-independently: EVERY thematic tag-parse pass
    over the `Clause` schema, merged. Each pass degrades on its own (build_tag_structured re-asks then
    omits-to-default); a failing pass leaves its group at defaults (never fails the whole clause). (The former
    aspect gate that pruned groups was removed in issue 0036 -- it measured a ~18% property-recall loss on Qwen.)

    `samples` (env `RAG_INGEST_CLAUSE_SAMPLES`, default 1) runs each group N times and UNIONs the LIST-valued
    fields across samples -- the inference-time fix for granite's list under-enumeration.

    `list_model` (env `RAG_INGEST_LIST_MODEL`, DEFAULT gemma) enables a CROSS-MODEL union: for LIST-bearing groups
    ONLY, also run a second (stronger, complementary) model and union its list values with the main model's.
    granite and gemma under-enumerate DIFFERENT items, so their union is more complete than either alone (it fixes
    the CONSISTENT misses same-model multi-sample can't); scoping the second model to list-bearing groups keeps its
    cost off the ~half of groups with no list field. Scalars prefer the main model (its results are unioned first).
    ON by default; disable with `RAG_INGEST_LIST_MODEL=off`."""
    n = samples if samples is not None else max(1, int(os.environ.get("RAG_INGEST_CLAUSE_SAMPLES", "1")))
    # cross-model list model: explicit ARGUMENT wins, else env, else the profile's general model (gemma). Any of
    # them may be "off"/"none"/"" to disable -- so the default-on model is configurable, never a buried hardcode.
    _raw = list_model if list_model is not None else os.environ.get("RAG_INGEST_LIST_MODEL", DEFAULT_GENERAL)
    lm = None if not _raw or str(_raw).strip().lower() in ("none", "off") else str(_raw).strip()
    stemp = temperature if n == 1 else max(temperature, 0.5)  # diversity across samples for the union to help

    async def _pass(fields: tuple[str, ...], model: str) -> Clause | None:
        try:
            return await build_tag_structured(
                model, Clause, fields=set(fields), temperature=stemp, label="clause-group",
            ).ainvoke(_GROUP_PROMPT.format(text=text))
        except Exception:  # noqa: BLE001 - a persistently-failing pass degrades to defaults, not a hard error
            return None

    async def _group(fields: tuple[str, ...]) -> dict[str, Any]:
        models = [model_id]  # main model first so its scalar values win 'first informative'
        if lm and lm != model_id and _group_has_list(fields):
            models.append(lm)  # cross-model union, LIST-bearing groups only (cost-scoped)
        runs = [r for m in models for r in await asyncio.gather(*[_pass(fields, m) for _ in range(n)])]
        return _combine_group(runs, fields)

    dicts = await asyncio.gather(*[_group(f) for f in CLAUSE_GROUPS.values()])
    merged: dict[str, Any] = {}
    for d in dicts:
        merged.update(d)
    merged["document_reference"] = document_reference or None
    return Clause(**merged)


def tag_extract_clause(text: str, model_id: str, *, document_reference: str = "",
                       temperature: float = 0.0, samples: int | None = None,
                       list_model: str | None = None) -> Clause:
    """Sync wrapper over `atag_extract_clause` (parity with the docling-graph `extract_clause`)."""
    return asyncio.run(atag_extract_clause(
        text, model_id, document_reference=document_reference, temperature=temperature,
        samples=samples, list_model=list_model))
