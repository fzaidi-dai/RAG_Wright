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
import re
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _deontic_cue_pattern() -> re.Pattern:
    from rag_wright.ontology.loader import load_deontic_cues

    cues = sorted(load_deontic_cues(), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(c) for c in cues) + r")\b", re.IGNORECASE)


def is_operative(text: str) -> bool:
    """ADR-0066 P3c (Gap 1): a section states an OPERATIVE rule iff its text carries a deontic CUE (must / shall /
    may / prohibited / ... -- authored in compliance_bridge.ttl `cmp:cue`). A section with NO cue is non-operative
    (a definitions / purpose / scope statement) and is skipped -- the domain-neutral, heading-agnostic replacement
    for the brittle 'definition'-in-heading keyword hack. Recall-first: any cue -> operative -> extracted."""
    return bool(text and _deontic_cue_pattern().search(text))


class RegulationAdapter:
    """The per-corpus seam (`CorpusAdapter`) for a regulation: an eCFR-style `sections.json`
    ([{section, heading, text}]) -> one `SourceDocument` per § section, carrying the section number and source
    as metadata (the extract stage reads them for the citation). The ONLY regulation-specific code in the path."""

    def __init__(self, sections_path: Any, source: str, *, limit: int = 0, skip_definitions: bool = True) -> None:
        self._path = sections_path
        self._source = source
        self._limit = limit
        # P3c (Gap 1): skip NON-OPERATIVE sections -- ones with no deontic cue (definitions / purpose / scope).
        # Extracting them over-generates spurious "requirements" (61 from FTC §255.0, ~40% of the KG, a leak
        # surface into judging). `is_operative` is the domain-neutral, ontology-driven cue gate that replaced the
        # brittle "definition"-in-heading keyword hack. `skip_definitions` keeps its name for back-compat.
        self._skip_definitions = skip_definitions

    def documents(self) -> Iterable[SourceDocument]:
        sections = json.loads(Path(self._path).read_text(encoding="utf-8"))
        if self._limit:
            sections = sections[: self._limit]
        for sec in sections:
            if not sec.get("text", "").strip():
                continue
            if self._skip_definitions and not is_operative(sec.get("text", "")):
                continue  # P3c: a section with no deontic cue is non-operative (definitions/purpose) -> skip
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
            if self._skip_definitions and not is_operative(sec.get("text") or ""):
                continue  # P3c: non-operative section (no deontic cue) -> skip
            # a headingless preamble section still ingests -- its citation is its position (never dropped)
            citation = sec.get("section") or str(i)
            yield SourceDocument(
                source_doc_id=canonical_source_doc_id(f"{self._source}_{citation}"),
                text=sec["text"],
                # issue 0043: carry the section's page provenance (from document_to_sections) to the extract stage
                # so each Requirement records its policy page(s). A pre-sectioned corpus (RegulationAdapter) has no
                # parse -> no pages, honestly absent.
                metadata={"section": citation, "source": self._source,
                          "pages": sec.get("pages") or [], "bbox": sec.get("bbox")},
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


def production_compliance_ingestion(store: Any, *, model: Any, extract_override: Optional[ExtractReqFn] = None,
                                    write_override: Optional[Any] = None, extraction_backend: str = "docling"):
    """Wire the real capabilities: extract = the requirement_extraction SUBGRAPH (CC-2, extract->adapt through
    the model seam -- Granite), write = `ComplianceStore(store).write_requirements` (ADR-0117 DD-1b: the Requirement
    KG write is a capability-layer extension over the generic store, not a store method). `extract_override` /
    `write_override` inject stubs for tests.

    `extraction_backend` (ADR-0119): "docling" (default, unchanged -- the per-section docling-graph LLM extraction)
    or "jev" (opt-in -- deterministic `operative_rule_spans` + one `jev_decision` call/span for the operative gate +
    actor + claim_types + cue-deontic + verbatim text; applicability/evidence_standard left empty, see the act)."""
    from rag_wright.capabilities.compliance_store import ComplianceStore
    from rag_wright.capabilities.requirement_extraction import ajev_extract_regulation_section
    from rag_wright.subgraphs.requirement_extraction import run_requirement_extraction

    req_extract_override = ajev_extract_regulation_section if extraction_backend == "jev" else None

    async def _extract(doc: SourceDocument) -> list:
        # COMP-ASYNC-1 lossless: raise_on_failure so a FAILED section propagates to the compliance `_aguard`
        # (-> retry -> dead-letter with reason), never silently writing 0 requirements. Genuine-empty still -> [].
        return await run_requirement_extraction(
            doc.text, model=model, source=doc.metadata["source"], section=doc.metadata["section"],
            pages=doc.metadata.get("pages") or [], bbox=doc.metadata.get("bbox"),  # issue 0043: policy page(s)
            raise_on_failure=True, extract_override=req_extract_override)

    write_fn = write_override or ComplianceStore(store).write_requirements

    async def _awrite(doc: SourceDocument, reqs: list) -> Any:
        return await asyncio.to_thread(write_fn, reqs)  # store I/O off the loop

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
    extract_override: Optional[ExtractReqFn] = None, write_override: Optional[Any] = None,
    extraction_backend: str = "docling",
) -> IngestionReport:
    """Ingest a regulation (`sections.json`) into the Requirement KG: ensure the compliance schema, then map
    every section through the per-section subgraph via the generic corpus driver (X/N progress, per-section
    dead-letter, is_done resume). Point `store` at the compliance database (`ragwright_compliance`).
    `extraction_backend` ("docling" default | "jev", ADR-0119) selects the requirement-extraction act."""
    store.ensure_compliance_schema()
    graph = production_compliance_ingestion(
        store, model=model, extract_override=extract_override, write_override=write_override,
        extraction_backend=extraction_backend)
    return await arun_corpus_ingestion(
        RegulationAdapter(sections_path, source), graph, is_done=_compliance_is_done(store, source))


async def run_compliance_document_ingestion(
    doc_name: str, data: bytes, store: Any, *, model: Any, source: str,
    sections_fn: Optional[Any] = None, extract_override: Optional[ExtractReqFn] = None,
    write_override: Optional[Any] = None,
) -> IngestionReport:
    """DOCPARSE-1: ingest a customer's OWN regulation/policy DOCUMENT (PDF/DOCX/HTML bytes) into the Requirement
    KG -- the same compliance pipeline, fed by a `DocumentRegulationAdapter` (docling parse -> heading-split
    sections) instead of a pre-sectioned eCFR `sections.json`. `sections_fn` injects the parse for tests."""
    store.ensure_compliance_schema()
    graph = production_compliance_ingestion(
        store, model=model, extract_override=extract_override, write_override=write_override)
    return await arun_corpus_ingestion(
        DocumentRegulationAdapter(doc_name, data, source, sections_fn=sections_fn), graph,
        is_done=_compliance_is_done(store, source))


def submit_compliance_ingestion(
    adapter: Any, store: Any, jobs: Any, *, job_id: str, model: Any, source: str,
    extract_override: Optional[ExtractReqFn] = None, write_override: Optional[Any] = None,
    max_concurrency: int = 4,
) -> str:
    """COMP-ASYNC-1 (ADR-0050): submit an ASYNC compliance ingestion job. Returns `job_id` IMMEDIATELY; sections
    ingest in the background with bounded parallelism, and a FAILED section is dead-lettered on the job (lossless).
    Ensures the compliance schema, builds the per-section graph, then hands the (adapter, graph) to the generic
    async runner. `adapter` = a RegulationAdapter (eCFR sections.json) OR DocumentRegulationAdapter (customer doc).
    `jobs` = the `JobStore`; poll `jobs.get(job_id)` for status."""
    from rag_wright.subgraphs.async_ingestion import submit_ingestion

    store.ensure_compliance_schema()
    graph = production_compliance_ingestion(
        store, model=model, extract_override=extract_override, write_override=write_override)
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


async def ainvoke(resources, inputs: dict):
    """EP-REF-1c (ADR-0118): the capability invoke factory (impl_ref target) for policy ingest into the Requirement
    KG. The store + the extraction model (STRUCTURED_REASONING, the quality-sensitive role) come from the workspace
    handle; the policy from `inputs`. Two source shapes: `{source, sections_path}` (a pre-sectioned eCFR-style
    `sections.json`) or `{source, doc_name, data}` (a policy DOCUMENT's raw bytes, split at its headings)."""
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.models.profiles import ModelRole

    model = default_extraction_model("requirement-extract", resources.model_id(ModelRole.STRUCTURED_REASONING))
    if inputs.get("data") is not None:  # a policy DOCUMENT (bytes)
        return await run_compliance_document_ingestion(
            inputs["doc_name"], inputs["data"], resources._store, model=model, source=inputs["source"])
    return await run_compliance_ingestion(  # a pre-sectioned sections.json
        inputs["sections_path"], resources._store, model=model, source=inputs["source"])
