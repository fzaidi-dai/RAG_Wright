"""RLM synthesis (FR-Q.5, T28): apply the recursive RLM method to the retrieved candidate chunks.

The query-side RLM. The rebuild (ADR-0015/0016) makes synthesis genuinely recursive dynamic sub-agents,
in two halves:

  - DESCENT (new, recursive): a `SliceExtractor` decomposes the candidate set through the T15 machinery
    (`build_rlm_agent`) — the interpreter holds the candidate chunks, a fresh `rlm_decomposer` splits an
    over-large group, and an `rlm_slice_worker` extracts the query-relevant facts from each leaf slice
    (per-slice tool use and per-slice skills live in the worker). A model is only ever called on a focused
    slice, never over the full candidate volume.
  - ASCENT (kept, ADR-0016): the Python `_reduce` fan-in combines the per-slice extracts into the final
    synthesis, recursively (each combine sees at most `fanout` notes), so a model is never called over the
    whole set of extracts either.

Unlike chunking, recursion IS gated for synthesis (ADR-0019): the ADR-0016 fail-if-absent recursion
discipline applies. The extractor and the combine both sit behind seams so the ascent is tested
hermetically with stubs and the descent machinery is driven by scripted fake models; the live extract +
synthesize is opt-in. No claim leaves without a citation: every `SliceOutput` carries its `chunk_id`
(FR-Q.6).
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model
from rag_wright.skills.rlm.agent import (
    RLM_DECOMPOSER,
    RLM_SLICE_WORKER,
    build_rlm_agent,
    rlm_interpreter_session,
)

__all__ = [
    "RLM_DECOMPOSER",
    "RLM_SLICE_WORKER",
    "SynthesisChunk",
    "SliceOutput",
    "SynthesisResult",
    "Synthesizer",
    "SeamSynthesizer",
    "SliceExtractor",
    "SeamSliceExtractor",
    "rlm_synthesize",
    "register_rlm_synthesis",
]

DEFAULT_REDUCE_CONCURRENCY = 8  # in-flight combine calls (backpressure); network-bound
DEFAULT_FANOUT = 8  # code-side reduce fan-in: a combine call sees at most this many notes at once

_EXTRACT_WORKER_PROMPT = (
    "You handle ONE slice of retrieved candidate passages. Extract only the facts in this slice that help "
    "answer the question, with any figures and named entities, faithfully and concisely, and keep each "
    "fact tied to the chunk_id it came from. If the slice is irrelevant, say so briefly. You never see the "
    "whole candidate set."
)
_EXTRACT_PROMPT = (
    "Extract only the facts in this passage that help answer the question, with any figures and named "
    "entities, faithfully and concisely. If the passage is irrelevant, say so briefly."
)
_COMBINE_PROMPT = (
    "Combine these notes into a single faithful synthesis that answers the question, keeping figures and "
    "named entities. Do not add facts not present in the notes."
)


class SynthesisChunk(BaseModel):
    """A candidate chunk to synthesize over: its id and text (fetched by the caller from the manifest)."""

    chunk_id: str
    text: str


class SliceOutput(BaseModel):
    """One slice's focused extract (the divide step), tied to its chunk_id (no claim without a citation)."""

    chunk_id: str
    extract: str


class SynthesisResult(BaseModel):
    """The RLM synthesis output: per-slice extracts, the combined synthesis, and the cited chunk_ids."""

    query: str
    slice_outputs: list[SliceOutput]
    synthesis: str
    chunk_ids: list[str]


# --- the ascent: the kept Python `_reduce` fan-in (ADR-0016) --------------------------------------


@runtime_checkable
class Synthesizer(Protocol):
    """The combine seam plus a direct single-slice extract used only by the coverage guarantee.

    `combine` folds a small group of already-reduced notes (<= fanout) into one synthesis (the ascent).
    `extract` extracts one candidate directly — used ONLY to cover a chunk the recursive descent missed
    (the completeness repair), never on the main path (that is the worker's job)."""

    def extract(self, query: str, text: str) -> str: ...
    def combine(self, query: str, extracts: list[str]) -> str: ...


class SeamSynthesizer:
    """The real combine + repair-extract: free-text calls through the model-profile seam (DeepSeek V4 Pro,
    ADR-0006)."""

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.STRUCTURED_REASONING)

    def extract(self, query: str, text: str) -> str:
        message = build_model(self._model_id).invoke(f"{_EXTRACT_PROMPT}\nQuestion: {query}\n\nPassage:\n{text}")
        return message.content if hasattr(message, "content") else str(message)

    def combine(self, query: str, extracts: list[str]) -> str:
        notes = "\n\n---\n\n".join(extracts)
        message = build_model(self._model_id).invoke(f"{_COMBINE_PROMPT}\nQuestion: {query}\n\nNotes:\n{notes}")
        return message.content if hasattr(message, "content") else str(message)


async def _reduce(
    query: str, extracts: list[str], synthesizer: Synthesizer, semaphore: asyncio.Semaphore, fanout: int
) -> str:
    """Fan-in reduce in code: combine at most `fanout` notes per call, recursing on the results so a
    model is never called over the whole set. Groups at one level are combined concurrently. Kept from
    the pre-rebuild implementation as the synthesis combine step (the ascent complements the descent)."""
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


# --- the descent: the recursive RLM extractor (the T15 machinery) ---------------------------------


@runtime_checkable
class SliceExtractor(Protocol):
    """The recursive-descent seam: decompose the candidate set and extract query-relevant facts per leaf."""

    def extract(self, query: str, chunks: list[SynthesisChunk]) -> list[SliceOutput]: ...


def _final_text(messages) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and (message.text or "").strip():
            return message.text
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "eval":
            return str(message.content)
    return ""


def _parse_slice_outputs(text: str) -> list[SliceOutput]:
    """Parse the last JSON array of {chunk_id, extract} objects from the model's final output."""
    end = text.rfind("]")
    start = text.rfind("[", 0, end)
    if start == -1 or end == -1:
        return []
    raw = json.loads(text[start : end + 1])
    return [SliceOutput(chunk_id=str(o["chunk_id"]), extract=str(o["extract"])) for o in raw]


class SeamSliceExtractor:
    """The real extractor: the candidate set is decomposed recursively via `build_rlm_agent` and each leaf
    slice is extracted by an `rlm_slice_worker`. Per-role models resolve through the profile seam
    (STRUCTURED_REASONING / DeepSeek V4 Pro) or are injected as instances (tests). `working_set` overrides
    the candidate view handed to the orchestrator (tests use an opaque handle to force recursion)."""

    def __init__(
        self,
        model: object = None,
        *,
        decomposer_model: object = None,
        worker_model: object = None,
        worker_tools=(),
        worker_skills=(),
        working_set: object = None,
    ) -> None:
        self._model = model
        self._decomposer_model = decomposer_model
        self._worker_model = worker_model
        self._worker_tools = worker_tools
        self._worker_skills = worker_skills
        self._working_set = working_set

    def _build_agent(self, chunks: list[SynthesisChunk], *, interpreter=None):
        model = self._model if self._model is not None else model_for(ModelRole.STRUCTURED_REASONING)
        return build_rlm_agent(
            reasoning_model=model,
            decomposer_model=self._decomposer_model if self._decomposer_model is not None else model,
            worker_model=self._worker_model if self._worker_model is not None else model,
            worker_system_prompt=_EXTRACT_WORKER_PROMPT,
            worker_tools=self._worker_tools,
            worker_skills=self._worker_skills,
            interpreter=interpreter,
        )

    def _working_set_tool(self, chunks: list[SynthesisChunk]):
        value = self._working_set if self._working_set is not None else [
            {"chunk_id": c.chunk_id, "text": c.text} for c in chunks
        ]

        @tool
        def working_set() -> object:
            """Return the working set: the retrieved candidate chunks (chunk_id, text)."""
            return value

        return working_set

    def _request(self, query: str) -> str:
        return (
            "Run this as a workflow. Call `const workingSet = await tools.workingSet();` to get the "
            "retrieved candidate set (a list of {chunk_id, text}); it is a JavaScript value in the "
            "interpreter, never in your context. Decompose it (dispatch rlm_decomposer for an over-large "
            "group and recurse), hand each leaf slice to an rlm_slice_worker that extracts the facts "
            "relevant to the question, keeping each fact tied to its chunk_id. Then return ONLY a JSON "
            "array [{\"chunk_id\": ..., \"extract\": ...}], one entry per candidate chunk.\n\n"
            f"Question: {query}"
        )

    def extract(self, query: str, chunks: list[SynthesisChunk]) -> list[SliceOutput]:
        if not chunks:
            return []
        request = self._request(query)
        # The candidate set is delivered as a PTC value (`tools.workingSet()`, T36): it stays in the
        # interpreter and never enters the model's context. The interpreter session is serialized
        # process-wide (KI-1, ADR-0020): build + run + teardown inside the lock.
        with rlm_interpreter_session(ptc=[self._working_set_tool(chunks)]) as interpreter:
            agent = self._build_agent(chunks, interpreter=interpreter)
            messages = agent.invoke({"messages": [HumanMessage(content=request)]})["messages"]
        return _parse_slice_outputs(_final_text(messages))


# --- the capability: descent then ascent ---------------------------------------------------------


def _guarantee_coverage(
    query: str,
    chunks: list[SynthesisChunk],
    outputs: list[SliceOutput],
    synthesizer: Synthesizer,
) -> list[SliceOutput]:
    """Coverage guarantee (T36 finding): out of context, the recursive descent can silently miss a deep
    leaf, dropping a candidate's facts while the run reports success. The code holds the whole candidate
    set, so it guarantees every `chunk_id` has an extract: any candidate the descent missed is extracted
    directly here (a bounded repair over only the missed chunks). Runtime guarantees coverage; the model
    reasons about grouping. Returns exactly one output per candidate, in candidate order."""
    by_id = {output.chunk_id: output for output in outputs}
    covered: list[SliceOutput] = []
    for chunk in chunks:
        output = by_id.get(chunk.chunk_id)
        if output is None:  # the descent missed this candidate out of context — cover it, do not drop it
            output = SliceOutput(chunk_id=chunk.chunk_id, extract=synthesizer.extract(query, chunk.text))
        covered.append(output)
    return covered


def rlm_synthesize(
    query: str,
    chunks: list[SynthesisChunk],
    *,
    extractor: Optional[SliceExtractor] = None,
    synthesizer: Optional[Synthesizer] = None,
    max_concurrency: int = DEFAULT_REDUCE_CONCURRENCY,
    fanout: int = DEFAULT_FANOUT,
) -> SynthesisResult:
    """Synthesize an answer over the candidate chunks: recursive extract (descent) then reduce (ascent).

    `extractor` decomposes the candidate set and extracts per leaf (defaults to the live
    `SeamSliceExtractor`; hermetic tests inject a stub); a code-side coverage guarantee then ensures every
    candidate has an extract even if the descent missed a deep leaf out of context (T36 finding); `_reduce`
    combines the extracts via `synthesizer` (defaults to `SeamSynthesizer`). Every extract keeps its
    `chunk_id`, so the result is cited (FR-Q.6).
    """
    if not chunks:
        return SynthesisResult(query=query, slice_outputs=[], synthesis="", chunk_ids=[])
    extractor = extractor if extractor is not None else SeamSliceExtractor()
    synthesizer = synthesizer if synthesizer is not None else SeamSynthesizer()

    slice_outputs = _guarantee_coverage(query, chunks, extractor.extract(query, chunks), synthesizer)
    semaphore = asyncio.Semaphore(max_concurrency)
    synthesis = asyncio.run(
        _reduce(query, [o.extract for o in slice_outputs], synthesizer, semaphore, fanout)
    )
    return SynthesisResult(
        query=query,
        slice_outputs=slice_outputs,
        synthesis=synthesis,
        chunk_ids=[chunk.chunk_id for chunk in chunks],
    )


def register_rlm_synthesis(registry: CapabilityRegistry) -> None:
    """Register RLM synthesis under FR-Q.5 (`rlm_synthesis`, an `agent_skill` applying the RLM method)."""
    registry.register(
        "rlm_synthesis",
        contract=SynthesisResult,
        kind="agent_skill",
        display_name="RLM synthesis",
    )
