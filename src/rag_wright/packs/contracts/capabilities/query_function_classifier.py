"""KG-5e lever (b): the taxonomy-constrained query->function classifier -- a SINGLE structured granite call.

KG-5c/5e proved that query-side function routing is the dominant retrieval lever and that the LegalBERT
classifier (trained on CLAUSE spans) is out-of-distribution on short query text. This routes instead with an
LLM that reads the query in-distribution, its output FORCED to the closed FUNCTION taxonomy (structured output,
then normalized at the boundary via `canonical_function`, dropping anything off-taxonomy -- the "strict
contracts, normalize at the boundary" rule). Unlike granite's free-text `clause_type` (KG-5b, unreliable), the
label space here is closed and ranked.

`route_query` is the KG-5e (b) front door: TWO separate granite calls -- one for the typed constraints, one
for the function -- on the hypothesis that granite does one task at a time better than both in one prompt (the
fallback, if this underperforms, is to unify into a single prompt). The structured factory is injectable so
the normalization is tested hermetically (no LLM, no network).
"""

from __future__ import annotations

from pydantic import BaseModel

from rag_wright.packs.contracts.schemas.function import FUNCTION_LABELS, canonical_function
from rag_wright.models.tag_structured import build_tag_structured  # ADR-0045: LLM-agnostic client-side output

_PROMPT = (
    "You match a legal question about a contract to clause types from a FIXED taxonomy. Return the EXACT "
    "taxonomy labels the question is about, MOST RELEVANT FIRST -- usually 1, at most 3, and only more than "
    "one when the question genuinely spans multiple types. Use only labels from the taxonomy; if none apply, "
    "return an empty list.\n\nTaxonomy:\n{taxonomy}\n\nQuestion: {query}"
)


class _FunctionChoice(BaseModel):
    """The LOOSE schema the LLM fills (ranked labels); normalized to canonical `FUNCTION_LABELS` at the boundary."""

    clause_types: list[str] = []


def _taxonomy_block() -> str:
    return "\n".join(f"- {label}" for label in FUNCTION_LABELS)


def classify_query_functions(
    query: str, model_id: str, *, k: int = 3, structured_factory=build_tag_structured
) -> list[str]:
    """The top-`k` canonical FUNCTION_LABELS a query is about, ranked, via one structured call. Off-taxonomy or
    unmappable labels are dropped; a failed structured emit (None or a persistent parse failure) -> ``[]``
    (degrades to no routing, never a hard error). Order preserved, deduped, truncated to `k`."""
    try:
        raw = structured_factory(model_id, _FunctionChoice).invoke(
            _PROMPT.format(taxonomy=_taxonomy_block(), query=query)
        )
    except Exception:  # noqa: BLE001 - client-side tag parse gave up -> no routing (degrade, never a hard error)
        return []
    if raw is None:
        return []
    out: list[str] = []
    for label in raw.clause_types:
        mapped = canonical_function(label)
        if mapped is not None and mapped not in out:
            out.append(mapped)
    return out[:k]


def route_query(
    query: str, *, extract_model, function_model_id: str, k: int = 3
) -> tuple[list[tuple[str, str]], list[str]]:
    """The KG-5e (b) query front door: TWO separate granite calls -> (typed constraints, ranked functions).

    Call 1 extracts the typed (dimension, value) constraints (docling-graph + `clause_template`); call 2
    classifies the function (this module). Two tasks, two calls -- if this underperforms a unified prompt, fold
    them. Kept as a single wrapper so production and the eval share one shape.
    """
    from rag_wright.packs.contracts.capabilities.dg_extraction import extract_clause
    from rag_wright.contracts.identifiers import ChunkId
    from rag_wright.packs.contracts.spans.clause_kg_extractor import clause_to_record

    constraints: list[tuple[str, str]] = []
    clause = extract_clause(query, extract_model)
    if clause is not None:
        rec = clause_to_record(clause, chunk_id=ChunkId.of("q", 0, query), function="Cap On Liability")
        constraints = [(a.dimension.value, a.value) for a in rec.assertions]
    functions = classify_query_functions(query, function_model_id, k=k)
    return constraints, functions


def register_query_function_classification(registry) -> None:
    """CAP-REG-2: register `query_function_classification` (agent_skill; taxonomy-constrained LLM classifier)."""
    from rag_wright.packs.contracts.schemas.function import FunctionClassification

    registry.register(
        "query_function_classification",
        contract=FunctionClassification,
        kind="agent_skill",
        display_name="Query function classification",
    )
