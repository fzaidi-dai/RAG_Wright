"""RLM synthesis (FR-Q.5, T28): apply the RLM method to the retrieved candidate chunks.

Applies the shared RLM divide-and-conquer method (FR-C.10, `rlm_method`, T15) on the query side: the
candidate chunks (from fusion, T27) are loaded into the interpreter as ordinary data, sliced in code
(one focused unit per chunk), a sub-model is called once per unit on that unit alone, and the per-unit
outputs are combined in code — recursively (a code-side fan-in reduce), so a model is only ever called
on a small focused slice or a small group of already-reduced notes, never over the full chunk volume.

The sub-calls are structured-under-reasoning work, so they resolve to the DeepSeek V4 Pro default via
the model-profile seam (ADR-0006); no model flag lives here. Dispatch is concurrent with backpressure
(the async + semaphore pattern, as in embedding/summarization): parallelizing the independent sub-calls
is the same tokens and cost but far less wall-clock (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model

DEFAULT_SYNTHESIS_CONCURRENCY = 8  # in-flight sub-calls (backpressure); network-bound
DEFAULT_FANOUT = 8  # code-side reduce fan-in: a combine call sees at most this many notes at once

_SLICE_PROMPT = (
    "Extract only the facts in this passage that help answer the question, with any figures and named "
    "entities, faithfully and concisely. If the passage is irrelevant, say so briefly."
)
_COMBINE_PROMPT = (
    "Combine these notes into a single faithful synthesis that answers the question, keeping figures and "
    "named entities. Do not add facts not present in the notes."
)


@runtime_checkable
class Synthesizer(Protocol):
    """The sub-model seam: a focused call on one slice, and a code-side combine over reduced notes."""

    def synthesize_slice(self, query: str, text: str) -> str: ...
    def combine(self, query: str, extracts: list[str]) -> str: ...


class SynthesisChunk(BaseModel):
    """A candidate chunk to synthesize over: its id and text (fetched by the caller from the manifest)."""

    chunk_id: str
    text: str


class SliceOutput(BaseModel):
    """One slice's focused sub-call result (the divide step)."""

    chunk_id: str
    extract: str


class SynthesisResult(BaseModel):
    """The RLM synthesis output: per-slice extracts, the combined synthesis, and the cited chunk_ids."""

    query: str
    slice_outputs: list[SliceOutput]
    synthesis: str
    chunk_ids: list[str]


class SeamSynthesizer:
    """The real sub-model: free-text focused calls through the model-profile seam (DeepSeek V4 Pro)."""

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.STRUCTURED_REASONING)

    def synthesize_slice(self, query: str, text: str) -> str:
        message = build_model(self._model_id).invoke(f"{_SLICE_PROMPT}\nQuestion: {query}\n\nPassage:\n{text}")
        return message.content if hasattr(message, "content") else str(message)

    def combine(self, query: str, extracts: list[str]) -> str:
        notes = "\n\n---\n\n".join(extracts)
        message = build_model(self._model_id).invoke(f"{_COMBINE_PROMPT}\nQuestion: {query}\n\nNotes:\n{notes}")
        return message.content if hasattr(message, "content") else str(message)


async def _reduce(
    query: str, extracts: list[str], synthesizer: Synthesizer, semaphore: asyncio.Semaphore, fanout: int
) -> str:
    """Fan-in reduce in code: combine at most `fanout` notes per call, recursing on the results so a
    model is never called over the whole set. Groups at one level are combined concurrently."""
    if not extracts:
        return ""
    if len(extracts) <= fanout:
        async with semaphore:
            return await asyncio.to_thread(synthesizer.combine, query, extracts)
    groups = [extracts[i : i + fanout] for i in range(0, len(extracts), fanout)]

    async def _combine(group: list[str]) -> str:
        async with semaphore:
            return await asyncio.to_thread(synthesizer.combine, query, group)

    partials = list(await asyncio.gather(*(_combine(group) for group in groups)))
    return await _reduce(query, partials, synthesizer, semaphore, fanout)


async def rlm_synthesize_async(
    query: str,
    chunks: list[SynthesisChunk],
    *,
    synthesizer: Synthesizer,
    max_concurrency: int = DEFAULT_SYNTHESIS_CONCURRENCY,
    fanout: int = DEFAULT_FANOUT,
) -> SynthesisResult:
    """Load chunks as data, sub-call once per chunk (concurrently, bounded), then reduce in code."""
    if not chunks:
        return SynthesisResult(query=query, slice_outputs=[], synthesis="", chunk_ids=[])
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _slice(chunk: SynthesisChunk) -> SliceOutput:
        async with semaphore:  # backpressure; each call sees only this chunk (never the full volume)
            extract = await asyncio.to_thread(synthesizer.synthesize_slice, query, chunk.text)
        return SliceOutput(chunk_id=chunk.chunk_id, extract=extract)

    slice_outputs = list(await asyncio.gather(*(_slice(chunk) for chunk in chunks)))
    synthesis = await _reduce(
        query, [output.extract for output in slice_outputs], synthesizer, semaphore, fanout
    )
    return SynthesisResult(
        query=query, slice_outputs=slice_outputs, synthesis=synthesis,
        chunk_ids=[chunk.chunk_id for chunk in chunks],
    )


def rlm_synthesize(
    query: str,
    chunks: list[SynthesisChunk],
    *,
    synthesizer: Synthesizer,
    max_concurrency: int = DEFAULT_SYNTHESIS_CONCURRENCY,
    fanout: int = DEFAULT_FANOUT,
) -> SynthesisResult:
    """Synchronous convenience for callers not already in an event loop."""
    return asyncio.run(
        rlm_synthesize_async(query, chunks, synthesizer=synthesizer,
                             max_concurrency=max_concurrency, fanout=fanout)
    )


def register_rlm_synthesis(registry: CapabilityRegistry) -> None:
    """Register RLM synthesis under FR-Q.5 (`rlm_synthesis`, an `agent_skill` applying the RLM method)."""
    registry.register(
        "rlm_synthesis",
        contract=SynthesisResult,
        kind="agent_skill",
        display_name="RLM synthesis",
    )
