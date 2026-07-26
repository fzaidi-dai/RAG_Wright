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
from rag_wright.models.seam import build_structured

_INTENTS = ("highlight", "extract", "discriminate")

_PROMPT = (
    "You map a user's natural-language question about a SINGLE known contract to a structured retrieval "
    "intent. The contract's clauses are typed by this fixed taxonomy:\n\n{taxonomy}\n\n"
    "Return:\n"
    "- clause_types: the taxonomy label(s) the question is about, using the EXACT strings above. Multiple "
    "are allowed. If the question is about a clause type NOT in the taxonomy, return an EMPTY list.\n"
    "- intent: 'highlight' to locate/show the clause(s); 'extract' if the user asks for a specific VALUE "
    "inside the clause (a date, a state, a party, an amount); 'discriminate' if the user wants the ONE "
    "clause matching a condition among several of the same type.\n"
    "- value_to_extract: for intent=extract, the value asked for (e.g. 'governing law state'); else null.\n"
    "- value_condition: for intent=discriminate, the selecting condition; else null.\n"
    "- in_taxonomy: false iff the question is about a clause type not in the taxonomy above.\n"
    "- confidence: 0..1, your confidence in this mapping.\n\n"
    "Question: {query}"
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
    structured_factory=build_structured,
    model_id: str | None = None,
) -> QueryIntent:
    """Parse a natural-language question into a `QueryIntent`: one structured LLM call, then boundary
    normalization. `in_taxonomy` is DERIVED from what actually maps (so a mapping miss degrades gracefully to
    semantic fallback, never a hard error). Uses STRUCTURED_REASONING (DeepSeek V4 Pro) via the seam."""
    model_id = model_id or model_for(ModelRole.STRUCTURED_REASONING)
    raw = structured_factory(model_id, _RawIntent).invoke(
        _PROMPT.format(taxonomy=_taxonomy_block(), query=query)
    )
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
