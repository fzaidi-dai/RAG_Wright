"""OKF signpost enrichment (FR-K.2, T46): category + one-line description per clause.

The one model-bearing step of the compile. For each clause it produces the two signposts a traversal
filters on without reading a body: the category (drives the directory tree and `tags`) and a one-line
description (the `index.md` entry text). ACORD ships no clause categories and its stored "summary" is the
full clause text, so both are manufactured here by a corpus-appropriate classifier through the
model-profile seam, NOT by `graph_extraction`'s fixed CUAD ontology (ADR-0022: 41-CUAD covers only 67% of
ACORD gold; a direct ACORD-9 classifier agrees 91.6%).

Model: a cheap model for this simple task ONLY (the `OKF_ENRICHMENT` profile role, ADR-0023). Everything
else in the system stays on its DeepSeek/Gemma role. The call is content-hash gated (keyed by `chunk_id`,
which embeds the
content hash) and concurrent (async + semaphore), per the parallel-LLM rule.
"""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_structured

# ACORD's own 9 attorney categories (the corpus-appropriate label set; ADR-0022). A different corpus
# supplies its own list.
ACORD_CATEGORIES = [
    "Limitation of Liability",
    "Indemnification",
    "Restrictive Covenants",
    "Governing Law",
    "Affirmative Covenants",
    "IP Ownership/License",
    "Term",
    "Liquidated Damages",
    "third party beneficiary clause",
]
_NONE = "None of these"


class AcordCat(str, Enum):
    LOL = "Limitation of Liability"
    INDEMN = "Indemnification"
    RESTRICT = "Restrictive Covenants"
    GOVLAW = "Governing Law"
    AFFIRM = "Affirmative Covenants"
    IP = "IP Ownership/License"
    TERM = "Term"
    LIQDAM = "Liquidated Damages"
    TPB = "third party beneficiary clause"
    NONE = _NONE


class ClauseEnrichment(BaseModel):
    """The structured classifier output: one category (or 'None of these') and a one-line description."""

    category: AcordCat
    description: str


class EnrichedClause(BaseModel):
    """One clause's signposts, keyed by chunk_id. `categorized` is False when the classifier abstained."""

    chunk_id: str
    category: str
    description: str
    categorized: bool


@runtime_checkable
class ClauseClassifier(Protocol):
    """Text -> {category, description}. The seam a test stubs so the compile is exercised without a model."""

    def __call__(self, text: str) -> ClauseEnrichment: ...


def enrichment_prompt(text: str, categories: list[str] = ACORD_CATEGORIES) -> str:
    """The classify-and-describe prompt (shared by the real classifier and the bench, so they match)."""
    return (
        "You are enriching a contract clause for a knowledge index.\n"
        "1) Classify it into exactly ONE category (or 'None of these' if none fit).\n"
        "2) Write a ONE-LINE description (<= 15 words) that a lawyer could use to tell this clause apart "
        "from others of the same category. Describe what THIS clause specifically says, not the category.\n\n"
        "Categories:\n- " + "\n- ".join(categories) + "\n\n"
        "Clause:\n" + text[:2000]
    )


class SeamClassifier:
    """The real classifier: structured output through the model-profile seam on the OKF_ENRICHMENT role.

    Retries a few times because a cheap model occasionally emits no valid tool call, which
    `with_structured_output` surfaces as a `None` return (not an exception); a bare `None` is a transient
    miss, so retry, and only raise when it persists (the caller skips a persistent failure and re-gates it).
    """

    def __init__(
        self, model_id: Optional[str] = None, categories: list[str] = ACORD_CATEGORIES, *, retries: int = 3
    ) -> None:
        self._runnable = build_structured(model_id or model_for(ModelRole.OKF_ENRICHMENT), ClauseEnrichment)
        self._categories = categories
        self._retries = retries

    def __call__(self, text: str) -> ClauseEnrichment:
        prompt = enrichment_prompt(text, self._categories)
        last_error: Exception | None = None
        for _ in range(self._retries):
            try:
                v = self._runnable.invoke(prompt)
            except Exception as e:  # noqa: BLE001 - transient provider/parse error; retry
                last_error = e
                continue
            if v is not None:
                return v
        raise last_error or ValueError("no structured output after retries")


async def enrich_all(
    texts: dict[str, str],
    classify: ClauseClassifier,
    *,
    concurrency: int = 8,
    cache: Optional[dict[str, EnrichedClause]] = None,
) -> dict[str, EnrichedClause]:
    """Enrich every chunk, reusing `cache` (content-hash gated by chunk_id) and classifying only the rest.

    Concurrent (async + semaphore) over the un-cached clauses. Determinism holds: the returned map is keyed
    by chunk_id regardless of completion order.
    """
    cache = cache or {}
    todo = {cid: text for cid, text in texts.items() if cid not in cache}
    sem = asyncio.Semaphore(concurrency)

    async def one(chunk_id: str, text: str) -> tuple[str, Optional[EnrichedClause]]:
        async with sem:
            try:
                v = await asyncio.to_thread(classify, text)
            except Exception:  # noqa: BLE001 - a rate-limited/failed call is skipped, not fatal;
                return chunk_id, None  # it stays un-cached so a gated re-run retries it
        if v is None:  # a classifier that yields no structured output is a skip, not a crash
            return chunk_id, None
        categorized = v.category != AcordCat.NONE
        return chunk_id, EnrichedClause(
            chunk_id=chunk_id,
            category=v.category.value if categorized else "",
            description=v.description,
            categorized=categorized,
        )

    fresh = {cid: ec for cid, ec in await asyncio.gather(*(one(c, t) for c, t in todo.items())) if ec}
    merged = {**cache, **fresh}
    return {cid: merged[cid] for cid in texts if cid in merged}  # excludes clauses that failed


def load_enrich_cache(path: Path, recipe_version: str) -> dict[str, EnrichedClause]:
    """Load the enrichment cache if it matches `recipe_version`; a recipe change invalidates it."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("recipe_version") != recipe_version:
        return {}
    return {cid: EnrichedClause.model_validate(e) for cid, e in data.get("entries", {}).items()}


def save_enrich_cache(path: Path, recipe_version: str, entries: dict[str, EnrichedClause]) -> None:
    """Persist the enrichment cache keyed by recipe_version (the content-hash gate's durable half)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recipe_version": recipe_version,
        "entries": {cid: e.model_dump() for cid, e in entries.items()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
