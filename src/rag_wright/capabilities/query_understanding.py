"""CU-C1: NL->type query understanding -- the front door of the CUAD highlight pipeline.

A natural-language question about a KNOWN contract is parsed in ONE structured LLM call into a `QueryIntent`:
which clause type(s) the user asks about (mapped to the FUNCTION taxonomy), what they want done (highlight /
extract a value / discriminate among same-type clauses), and whether the ask is in-taxonomy.

This is a user-required MVP front door (robust natural-language handling, NOT templates), layered ON TOP of the
registered capabilities. It is intentionally NOT registered under a canonical capability slug: NL->type is not
an FR-C/FR-Q capability in the (closed) spec catalog, and registering one would invent a requirement the spec
does not state. It is ordinary tested software the compiled query graph can call as glue.

The strict `QueryIntent` validator rejects non-taxonomy labels, so the LLM emits a LOOSE schema and this module
NORMALIZES at the boundary (canonicalize labels, drop unmappable ones, derive `in_taxonomy`) -- the "strict
contracts, normalize at the boundary" rule. Multi-type is allowed. Out-of-taxonomy (nothing maps) ->
`in_taxonomy=False`, `clause_types=[]` -> the serve stage does semantic fallback + a low-confidence flag.
`structured_factory` is injectable so the mapping logic is tested hermetically (no LLM, no network).
"""

from __future__ import annotations

from pydantic import BaseModel

from rag_wright.contracts.function import FUNCTION_LABELS, canonical_function
from rag_wright.contracts.query_intent import QueryIntent
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model
from rag_wright.models.tag_structured import build_tag_structured  # ADR-0045: LLM-agnostic client-side output

_INTENTS = ("highlight", "extract", "discriminate")

# Two-step (reason -> emit), the sanctioned pattern for a model that cannot combine reasoning with a forced
# structured call (CLAUDE.md standing rule; ADR-0006 Qwen precedent; ADR-0032). Step 1 reasons in free text
# (a GENERAL-model strength -- no forced tool, so no thinking-mode tool rejection); step 2 emits the schema
# from that reasoning (the profile disables thinking on this forced call for the models that need it, e.g.
# Gemma). This makes NL->type work on the cheap GENERAL model, not just DeepSeek Pro (benchmarked in CU-D2).
_REASON_PROMPT = (
    "You match a user's natural-language question about a SINGLE known contract to clause types from a fixed "
    "taxonomy. Reason briefly about which type(s) the question concerns and what the user wants, then END "
    "with EXACTLY these three lines:\n"
    "TYPES: <comma-separated EXACT taxonomy labels the question is about, or NONE if the concept is absent "
    "from the taxonomy>\n"
    "INTENT: <highlight to locate the clause | extract if the user asks for a specific value inside it | "
    "discriminate if the user wants the one clause matching a condition among several of the same type>\n"
    "VALUE: <the value to extract or the selecting condition, or NONE>\n\n"
    "Taxonomy:\n{taxonomy}\n\nQuestion: {query}"
)
_EMIT_PROMPT = (
    "Convert this analysis into the structured intent. clause_types = the EXACT labels listed after TYPES "
    "(empty list if TYPES is NONE). intent = the word after INTENT. value_to_extract = the VALUE when "
    "intent is extract, else null; value_condition = the VALUE when intent is discriminate, else null. "
    "in_taxonomy = false iff TYPES is NONE.\n\nAnalysis:\n{reasoning}"
)


class _RawIntent(BaseModel):
    """The LOOSE schema the LLM fills; normalized into the strict `QueryIntent` at the boundary."""

    clause_types: list[str] = []
    intent: str = "highlight"
    value_to_extract: str | None = None
    value_condition: str | None = None
    in_taxonomy: bool = True
    confidence: float = 1.0


def _taxonomy_block() -> str:
    return "\n".join(f"- {label}" for label in FUNCTION_LABELS)


def understand_query(
    query: str,
    *,
    reason_factory=build_model,
    structured_factory=build_tag_structured,
    model_id: str | None = None,
) -> QueryIntent:
    """Parse a natural-language question into a `QueryIntent` via the two-step reason->emit (see the prompts
    above): step 1 reasons in free text, step 2 emits the schema from that reasoning. Then boundary
    normalization: `in_taxonomy` is DERIVED from what actually maps (a mapping miss degrades gracefully to
    semantic fallback, never a hard error); a failed emit (None) degrades to out-of-taxonomy low-confidence.
    Defaults to the GENERAL model (Gemma): the two-step ties/beats DeepSeek Pro on NL->type at ~3x less
    latency and cost, and is not throttled (benchmarked CU-D2 / ADR-0032). Factories are injectable for
    hermetic tests."""
    model_id = model_id or model_for(ModelRole.GENERAL)
    reasoning = reason_factory(model_id).invoke(
        _REASON_PROMPT.format(taxonomy=_taxonomy_block(), query=query)
    ).content
    try:
        raw = structured_factory(model_id, _RawIntent).invoke(_EMIT_PROMPT.format(reasoning=reasoning))
    except Exception:  # noqa: BLE001 - a persistent client-side parse failure degrades like a None emit
        raw = None
    if raw is None:  # the emit failed -> out-of-taxonomy, low confidence (never a hard error)
        return QueryIntent(clause_types=[], intent="highlight", in_taxonomy=False, confidence=0.0)
    canon: list[str] = []
    for label in raw.clause_types:
        mapped = canonical_function(label)
        if mapped is not None and mapped not in canon:
            canon.append(mapped)
    in_taxonomy = bool(canon)  # ground truth = did anything map; the LLM's own flag is advisory only
    intent = raw.intent if raw.intent in _INTENTS else "highlight"
    confidence = min(1.0, max(0.0, raw.confidence))
    return QueryIntent(
        clause_types=canon,
        intent=intent,
        value_to_extract=raw.value_to_extract if intent == "extract" else None,
        value_condition=raw.value_condition if intent == "discriminate" else None,
        in_taxonomy=in_taxonomy,
        confidence=confidence,
    )
