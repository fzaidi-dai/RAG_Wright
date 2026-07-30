"""LG-2: `semantic_chunking` as a hardened, GRANULAR LangGraph subgraph.

The single-call (non-agentic) chunker, expressed as explicit nodes -- the multi-step pipeline is the graph,
not a hidden orchestrator:

    gate --cached--> END
      | (miss: load_document)
      v
    discover [RetryPolicy + error_handler -> dead-letter]  --dead_letter--> END
      v
    finalize (validate partition + _finalize_chunks)  --dead_letter--> END
      v
    summarize (async, concurrent per-chunk summaries)
      v
    manifest (build chunks + validate boundaries + write cache) --> END

- **gate**: content-hash short-circuit -- a cached manifest is returned with no LLM call.
- **discover**: the ONE LLM step (single-call boundary discovery). A transient blip is retried
  (`TransientExtraction` -> DEFAULT_RETRY); on retry exhaustion the `error_handler` dead-letters the document
  (skip it -- never lose the batch) rather than raising.
- **finalize / manifest**: the deterministic layer, reused verbatim from `rlm_chunking` (`_finalize_chunks`,
  `_validate_partition`, `_validate_boundaries`, `ChunkManifest`).
- **summarize**: a sync node using `asyncio.run` for the concurrent per-chunk summaries (matches
  `rlm_chunking.chunk`). `asyncio.run` copies the current contextvars context, so the OTel trace context is
  preserved (it is NOT a context-detached loop); the summarizer goes through the LangChain seam
  (auto-captured), and its `to_thread` fan-out is carried by GraphWright's threading instrumentation.
- **observability**: `discover` is wrapped in `raw_llm_span` (raw-SDK), `summarize` in `business_span`.

`discoverer` / `summarizer` are the same injected seams as `rlm_chunking.chunk`, so the graph is hermetically
testable with the existing stubs. The default discoverer is the deterministic single-call one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag_wright.capabilities.parsing import ParsedDocument, load_document
from rag_wright.capabilities.rlm_chunking import (
    DEFAULT_SUMMARY_CONCURRENCY,
    DEFAULT_TOKEN_CAP,
    BoundaryDiscoverer,
    BoundaryValidationError,
    Chunk,
    ChunkManifest,
    SeamSummarizer,
    SingleCallBoundaryDiscoverer,
    Summarizer,
    _chunk_offsets,
    _estimate_tokens,
    _finalize_chunks,
    _summarize_all,
    _validate_boundaries,
    _validate_partition,
)
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span, dead_letter, raw_llm_span
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction


class SemanticChunkingState(TypedDict, total=False):
    parsed: ParsedDocument
    cache_dir: Path
    token_cap: int
    max_concurrency: int
    document: Any
    spans: list
    texts: list
    offsets: list
    summaries: list
    manifest: Optional[ChunkManifest]
    dead_letter: Optional[dict]


def _manifest_path(state: SemanticChunkingState) -> Path:
    parsed = state["parsed"]
    return state["cache_dir"] / f"{parsed.source_doc_id}.{parsed.content_hash[:16]}.chunks.json"


def build_semantic_chunking(
    discoverer: Optional[BoundaryDiscoverer] = None,
    summarizer: Optional[Summarizer] = None,
    *,
    model_id: Optional[str] = None,
    retry_policy: Any = DEFAULT_RETRY,
):
    """Compile the `semantic_chunking` subgraph. `discoverer` / `summarizer` are injected (defaulting to the
    live single-call discoverer + seam summarizer), matching `rlm_chunking.chunk`. `retry_policy` is the
    discover node's policy (overridable for fast tests)."""

    discoverer = discoverer if discoverer is not None else SingleCallBoundaryDiscoverer(model_id)
    summarizer = summarizer if summarizer is not None else SeamSummarizer()
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    def gate(state: SemanticChunkingState) -> SemanticChunkingState:
        path = _manifest_path(state)
        if path.exists():  # content-hash gate: reuse, no LLM
            return {"manifest": ChunkManifest.model_validate_json(path.read_text(encoding="utf-8"))}
        state["cache_dir"].mkdir(parents=True, exist_ok=True)
        return {"document": load_document(state["parsed"])}

    def discover(state: SemanticChunkingState, runtime: Runtime) -> SemanticChunkingState:
        # node_attempt is 1-indexed; on the FINAL attempt a failure dead-letters the document (skip it, never
        # lose the batch) instead of raising; earlier attempts re-raise so the RetryPolicy retries.
        attempt = runtime.execution_info.node_attempt
        with raw_llm_span("semantic_chunking.discover", model=str(model_id or "single-call")):
            try:
                spans = discoverer.discover(state["document"])
            except Exception as exc:  # noqa: BLE001
                if attempt >= max_attempts:
                    return {"dead_letter": dead_letter(
                        "boundary_discovery_failed", source_doc_id=state["parsed"].source_doc_id, error=str(exc))}
                raise TransientExtraction(str(exc)) from exc
        return {"spans": spans}

    def finalize(state: SemanticChunkingState) -> SemanticChunkingState:
        document, spans = state["document"], state["spans"]
        token_cap = state.get("token_cap", DEFAULT_TOKEN_CAP)
        try:
            _validate_partition(spans, len(document.texts))
            texts = _finalize_chunks(document, spans, token_cap)
        except BoundaryValidationError as exc:
            return {"dead_letter": dead_letter(
                "boundary_validation_failed", source_doc_id=state["parsed"].source_doc_id, error=str(exc))}
        return {"texts": texts, "offsets": _chunk_offsets(texts)}

    def summarize(state: SemanticChunkingState) -> SemanticChunkingState:
        # Sync node using asyncio.run (matches rlm_chunking.chunk). asyncio.run copies the current contextvars
        # context, so the OTel trace context is preserved; the to_thread summary fan-out is carried by
        # GraphWright's threading instrumentation. Works under both invoke and ainvoke (a sync node runs in a
        # worker thread with no live loop during ainvoke).
        with business_span("semantic_chunking.summarize", chunk_count=len(state["texts"])):
            summaries = asyncio.run(_summarize_all(
                state["texts"], summarizer, state.get("max_concurrency", DEFAULT_SUMMARY_CONCURRENCY)))
        return {"summaries": summaries}

    def manifest(state: SemanticChunkingState) -> SemanticChunkingState:
        parsed = state["parsed"]
        texts, summaries, offsets = state["texts"], state["summaries"], state["offsets"]
        token_cap = state.get("token_cap", DEFAULT_TOKEN_CAP)
        chunks = [
            Chunk(
                chunk_id=ChunkId.of(parsed.source_doc_id, i, text).value,
                chunk_index=i,
                text=text,
                summary=summary,
                token_estimate=_estimate_tokens(text),
                doc_start=offsets[i][0],
                doc_end=offsets[i][1],
            )
            for i, (text, summary) in enumerate(zip(texts, summaries))
        ]
        _validate_boundaries(chunks, token_cap)
        result = ChunkManifest(
            source_doc_id=parsed.source_doc_id, content_hash=parsed.content_hash, token_cap=token_cap, chunks=chunks)
        _manifest_path(state).write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return {"manifest": result}

    g = StateGraph(SemanticChunkingState)
    g.add_node("gate", gate)
    g.add_node("discover", discover, retry_policy=retry_policy)
    g.add_node("finalize", finalize)
    g.add_node("summarize", summarize)
    g.add_node("manifest", manifest)

    g.add_edge(START, "gate")
    g.add_conditional_edges("gate", lambda s: "end" if s.get("manifest") else "discover",
                            {"discover": "discover", "end": END})
    g.add_conditional_edges("discover", lambda s: "end" if s.get("dead_letter") else "finalize",
                            {"finalize": "finalize", "end": END})
    g.add_conditional_edges("finalize", lambda s: "end" if s.get("dead_letter") else "summarize",
                            {"summarize": "summarize", "end": END})
    g.add_edge("summarize", "manifest")
    g.add_edge("manifest", END)
    return g.compile()


def register_semantic_chunking_subgraph(registry) -> None:
    """LG-2: register `semantic_chunking` (subgraph). The manifest/slug already exist (CAP-REG-1b); this binds
    the LangGraph runnable's contract (ChunkManifest)."""
    registry.register(
        "semantic_chunking",
        contract=ChunkManifest,
        kind="subgraph",
        display_name="Semantic chunking (single-call)",
    )
