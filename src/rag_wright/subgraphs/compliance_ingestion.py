"""CC-5 (compliance §13 C-2): the `compliance_ingestion` subgraph -- regulatory corpus -> Requirement KG.

A hardened LangGraph subgraph on `scaffold.py`, following the LG-3 pattern: per § section, extract the deontic
rules (requirement_extraction, CC-2) and write them as `Requirement` nodes, with retry -> dead-letter per
section so one bad section never kills the ingest. It REUSES the generic ingestion machinery -- `SourceDocument`,
the `CorpusAdapter` seam, and the `run_corpus_ingestion` driver (X/N progress + per-doc dead-letter + is_done
resume) -- via a thin `RegulationAdapter`; only the two per-section stages (extract, write) are compliance-specific.

The Requirement KG lives in its OWN database (`ragwright_compliance`), so the contract KG stays clean; the store
adds the `Requirement` vertex type additively (`ensure_compliance_schema`).
"""

from __future__ import annotations

import asyncio

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag_wright.contracts.identifiers import canonical_source_doc_id
from rag_wright.subgraphs.contract_ingestion_pipeline import (
    IngestionReport,
    SourceDocument,
    arun_corpus_ingestion,
)
from rag_wright.subgraphs.scaffold import DEFAULT_RETRY, business_span, dead_letter
from rag_wright.subgraphs.typed_clause_extraction import TransientExtraction

# extract_fn: a section's SourceDocument -> its extracted Requirement[]; write_fn: (doc, reqs) -> count written.
ExtractReqFn = Callable[[SourceDocument], list]
WriteReqFn = Callable[[SourceDocument, list], int]


class RegulationAdapter:
    """The per-corpus seam (`CorpusAdapter`) for a regulation: an eCFR-style `sections.json`
    ([{section, heading, text}]) -> one `SourceDocument` per § section, carrying the section number and source
    as metadata (the extract stage reads them for the citation). The ONLY regulation-specific code in the path."""

    def __init__(self, sections_path: Any, source: str, *, limit: int = 0, skip_definitions: bool = True) -> None:
        self._path = sections_path
        self._source = source
        self._limit = limit
        # EXTRACT-TUNE: a "Purpose and definitions" section states NO operative deontic rules -- extracting it
        # over-generates spurious "requirements" (61 from FTC §255.0, ~40% of the KG, a leak surface into
        # judging). Skip any section whose heading indicates definitions (universally non-operative). The robust
        # general version is a deontic-cue/SHACL validity gate ([[ontology-lever-vs-extraction-lever]]).
        self._skip_definitions = skip_definitions

    def documents(self) -> Iterable[SourceDocument]:
        sections = json.loads(Path(self._path).read_text(encoding="utf-8"))
        if self._limit:
            sections = sections[: self._limit]
        for sec in sections:
            if not sec.get("text", "").strip():
                continue
            if self._skip_definitions and "definition" in sec.get("heading", "").lower():
                continue  # skip a definitions section (no operative rules)
            yield SourceDocument(
                source_doc_id=canonical_source_doc_id(f"{self._source}_{sec['section']}"),
                text=sec["text"],
                metadata={"section": sec["section"], "source": self._source},
            )


class DocumentRegulationAdapter:
    """DOCPARSE-1 (ADR-0049): the per-corpus seam for a customer's OWN regulation/policy DOCUMENT (PDF/DOCX/HTML),
    not a pre-sectioned eCFR `sections.json`. Parses the document once (docling) and splits it at its headings via
    `document_to_sections`, yielding one `SourceDocument` per section -- the SAME shape `RegulationAdapter` yields,
    so a customer policy PDF flows through the identical compliance pipeline. `sections_fn` is injected (the docling
    parse) so this is hermetically testable; production passes the real `document_to_sections(parse_document_bytes(...))`."""

    def __init__(self, doc_name: str, data: bytes, source: str, *, sections_fn: Any = None,
                 limit: int = 0, skip_definitions: bool = True) -> None:
        self._name = doc_name
        self._data = data
        self._source = source
        self._sections_fn = sections_fn
        self._limit = limit
        self._skip_definitions = skip_definitions

    def documents(self) -> Iterable[SourceDocument]:
        if self._sections_fn is not None:
            sections = self._sections_fn(self._name, self._data)
        else:  # production: docling parse -> heading-split sections (DOCPARSE-1)
            from rag_wright.corpus.document_parser import document_to_sections, parse_document_bytes

            sections = document_to_sections(parse_document_bytes(self._name, self._data))
        if self._limit:
            sections = sections[: self._limit]
        for i, sec in enumerate(sections, 1):
            if not (sec.get("text") or "").strip():
                continue
            if self._skip_definitions and "definition" in (sec.get("heading") or "").lower():
                continue
            # a headingless preamble section still ingests -- its citation is its position (never dropped)
            citation = sec.get("section") or str(i)
            yield SourceDocument(
                source_doc_id=canonical_source_doc_id(f"{self._source}_{citation}"),
                text=sec["text"],
                metadata={"section": citation, "source": self._source},
            )


class ComplianceIngestState(TypedDict, total=False):
    document: SourceDocument
    requirements: list
    written: dict
    dead_letter: Optional[dict]


def build_compliance_ingest(
    extract_fn: ExtractReqFn, write_fn: WriteReqFn, *, retry_policy: Any = DEFAULT_RETRY
):
    """Compile the per-section ingest subgraph: START -> extract[retry] -> write[retry] -> END. Both stages are
    injected for hermetic testing. A stage failure dead-letters the section (dropped with a reason, never
    raised) so one bad section never kills the corpus ingest -- the LG-3 hardening pattern."""
    max_attempts = int(getattr(retry_policy, "max_attempts", 3))

    async def _aguard(name: str, work: Any, runtime: Runtime, doc: SourceDocument) -> dict:
        attempt = runtime.execution_info.node_attempt
        with business_span(f"compliance_ingestion.{name}", source_doc_id=doc.source_doc_id):
            try:
                return await work()
            except Exception as exc:  # noqa: BLE001 - transient -> retry, or dead-letter on exhaustion
                if attempt >= max_attempts:
                    return {"dead_letter": dead_letter(
                        "ingest_failed", source_doc_id=doc.source_doc_id, stage=name, error=str(exc))}
                raise TransientExtraction(str(exc)) from exc

    async def extract(state: ComplianceIngestState, runtime: Runtime) -> ComplianceIngestState:
        doc = state["document"]

        async def _w() -> dict:
            return {"requirements": await extract_fn(doc)}

        return await _aguard("extract", _w, runtime, doc)

    async def write(state: ComplianceIngestState, runtime: Runtime) -> ComplianceIngestState:
        if state.get("dead_letter"):
            return {}
        doc = state["document"]

        async def _w() -> dict:
            return {"written": {"requirements": await write_fn(doc, state.get("requirements", []))}}

        return await _aguard("write", _w, runtime, doc)

    g = StateGraph(ComplianceIngestState)
    g.add_node("extract", extract, retry_policy=retry_policy)
    g.add_node("write", write, retry_policy=retry_policy)
    g.add_edge(START, "extract")
    g.add_conditional_edges("extract", lambda s: "end" if s.get("dead_letter") else "write",
                            {"write": "write", "end": END})
    g.add_edge("write", END)
    return g.compile()


def production_compliance_ingestion(store: Any, *, model: Any, extract_override: Optional[ExtractReqFn] = None):
    """Wire the real capabilities: extract = the requirement_extraction SUBGRAPH (CC-2, extract->adapt through
    the model seam -- Granite), write = `store.write_requirements`. `extract_override` injects a stub for tests."""
    from rag_wright.subgraphs.requirement_extraction import run_requirement_extraction

    async def _extract(doc: SourceDocument) -> list:
        # COMP-ASYNC-1 lossless: raise_on_failure so a FAILED section propagates to the compliance `_aguard`
        # (-> retry -> dead-letter with reason), never silently writing 0 requirements. Genuine-empty still -> [].
        return await run_requirement_extraction(
            doc.text, model=model, source=doc.metadata["source"], section=doc.metadata["section"],
            raise_on_failure=True)

    async def _awrite(doc: SourceDocument, reqs: list) -> Any:
        return await asyncio.to_thread(store.write_requirements, reqs)  # store I/O off the loop

    return build_compliance_ingest(extract_override or _extract, _awrite)


def _compliance_is_done(store: Any, source: str) -> Any:
    """PROD-2 #2 resume: skip a SECTION already ingested for `source` (a present `citation` in the Requirement KG
    -- the compliance analogue of a present `Contract` node). Computed ONCE (one query); a failed/empty section
    wrote no requirement, so it is absent and correctly re-runs. The section's citation is `§ {section}` (matches
    `to_requirements`)."""
    done = store.ingested_citations(source)
    return lambda doc: f"§ {doc.metadata.get('section', '')}" in done


async def run_compliance_ingestion(
    sections_path: Any, store: Any, *, model: Any, source: str = "FTC 16 CFR 255",
    extract_override: Optional[ExtractReqFn] = None,
) -> IngestionReport:
    """Ingest a regulation (`sections.json`) into the Requirement KG: ensure the compliance schema, then map
    every section through the per-section subgraph via the generic corpus driver (X/N progress, per-section
    dead-letter, is_done resume). Point `store` at the compliance database (`ragwright_compliance`)."""
    store.ensure_compliance_schema()
    graph = production_compliance_ingestion(store, model=model, extract_override=extract_override)
    return await arun_corpus_ingestion(
        RegulationAdapter(sections_path, source), graph, is_done=_compliance_is_done(store, source))


async def run_compliance_document_ingestion(
    doc_name: str, data: bytes, store: Any, *, model: Any, source: str,
    sections_fn: Optional[Any] = None, extract_override: Optional[ExtractReqFn] = None,
) -> IngestionReport:
    """DOCPARSE-1: ingest a customer's OWN regulation/policy DOCUMENT (PDF/DOCX/HTML bytes) into the Requirement
    KG -- the same compliance pipeline, fed by a `DocumentRegulationAdapter` (docling parse -> heading-split
    sections) instead of a pre-sectioned eCFR `sections.json`. `sections_fn` injects the parse for tests."""
    store.ensure_compliance_schema()
    graph = production_compliance_ingestion(store, model=model, extract_override=extract_override)
    return await arun_corpus_ingestion(
        DocumentRegulationAdapter(doc_name, data, source, sections_fn=sections_fn), graph,
        is_done=_compliance_is_done(store, source))


def submit_compliance_ingestion(
    adapter: Any, store: Any, jobs: Any, *, job_id: str, model: Any, source: str,
    extract_override: Optional[ExtractReqFn] = None, max_concurrency: int = 4,
) -> str:
    """COMP-ASYNC-1 (ADR-0050): submit an ASYNC compliance ingestion job. Returns `job_id` IMMEDIATELY; sections
    ingest in the background with bounded parallelism, and a FAILED section is dead-lettered on the job (lossless).
    Ensures the compliance schema, builds the per-section graph, then hands the (adapter, graph) to the generic
    async runner. `adapter` = a RegulationAdapter (eCFR sections.json) OR DocumentRegulationAdapter (customer doc).
    `jobs` = the `JobStore`; poll `jobs.get(job_id)` for status."""
    from rag_wright.subgraphs.async_ingestion import submit_ingestion

    store.ensure_compliance_schema()
    graph = production_compliance_ingestion(store, model=model, extract_override=extract_override)
    return submit_ingestion(
        adapter, graph, jobs, job_id=job_id, db=getattr(store, "database", ""),
        corpus_ref={"kind": "compliance", "source": source}, max_concurrency=max_concurrency,
        is_done=_compliance_is_done(store, source))  # PROD-2 #2: skip already-ingested sections on resume


def register_compliance_ingestion(registry) -> None:
    """Register `compliance_ingestion` (subgraph; CC-5). Contract = `IngestionReport`."""
    registry.register(
        "compliance_ingestion",
        contract=IngestionReport,
        kind="subgraph",
        display_name="Compliance ingestion (regulatory corpus -> Requirement KG)",
    )
