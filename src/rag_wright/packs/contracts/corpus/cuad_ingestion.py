"""CUAD ingestion: the reference `CorpusAdapter` + the CUAD ingest driver.

Relocated out of the GENERIC `subgraphs.contract_ingestion_pipeline` (which stays corpus-agnostic) so the
dataset-specific adapter and driver live with the other per-corpus code in `corpus/`. The generic machinery
(the `CorpusAdapter` seam, `run_corpus_ingestion`, `production_document_ingest`, and the cache-seed helpers) is
imported from the pipeline; only the CUAD parsing + wiring lives here. Adding another corpus = a sibling adapter
+ driver here, never touching the generic pipeline.
"""

from __future__ import annotations

from typing import Any, Iterable

from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import (
    IngestionReport,
    SourceDocument,
    aproduction_document_ingest,
    arun_corpus_ingestion,
    seed_chunk_cache,
)


class CuadAdapter:
    """The REFERENCE `CorpusAdapter` (ADR-locked design): CUAD -> `SourceDocument`s. It is the ONLY CUAD-specific
    code in the ingest path -- it parses the corpus (CUAD ships text in JSON, so no docling parse), assigns the
    ONE canonical `source_doc_id` (HYG-1), and passes the raw title as metadata. Adding another corpus means
    writing a sibling adapter (e.g. `AcordAdapter` carrying pre-segmented spans in `metadata`); the pipeline and
    driver do not change. `run_corpus_ingestion(CuadAdapter(path), ingest_graph, link_fn=...)` ingests it."""

    def __init__(self, cuad_path: Any, *, limit: int = 0) -> None:
        self._path = cuad_path
        self._limit = limit

    def documents(self) -> Iterable[SourceDocument]:
        from rag_wright.pack_sdk import canonical_source_doc_id
        from rag_wright.packs.contracts.spans.cuad_labels import parse_cuad

        contracts = list(parse_cuad(self._path))
        if self._limit:
            contracts = contracts[: self._limit]
        for contract in contracts:
            yield SourceDocument(
                source_doc_id=canonical_source_doc_id(contract.contract_id),
                text=contract.context,
                metadata={"raw_title": contract.contract_id},
            )


async def arun_cuad_ingestion(cuad_path: Any, store: Any, *, cache_dir: Any, limit: int = 0) -> IngestionReport:
    """INGEST-REFACTOR proof: ingest CUAD through the GENERIC pipeline + `CuadAdapter` -- one call, no
    `ingest_cuad()`. Builds the EDGAR registry, ensures the schema, ingests `limit` documents. Point `store` at a SCRATCH database for a non-destructive smoke.

    INGEST-REFACTOR (a) cache reuse (the ONLY CUAD-specific wiring): the pipeline reuses GP-1B's party
    extractions (`dg_extracted_parties.json`, ~482) via `party_seed_path`, and prior `chunk()` manifests
    (`data/cache/cuad/chunks`, content-hash keyed so only true matches are reused) copied into the run's cache.
    The unavoidable cost that remains is clause property extraction (the template changed since those were cached)."""
    import json
    from pathlib import Path

    from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore
    from rag_wright.packs.contracts.capabilities.dg_extraction import build_verified_registry

    store.ensure_schema()
    seed_chunk_cache(Path(cache_dir) / "chunks", Path("data/cache/cuad/chunks"))
    vset = json.loads(Path("data/edgar/verification_set.json").read_text(encoding="utf-8"))
    ingest_graph = aproduction_document_ingest(
        store, cache_dir=cache_dir, registry=build_verified_registry(vset),
        party_seed_path="data/cache/dg_extracted_parties.json")
    return await arun_corpus_ingestion(
        CuadAdapter(cuad_path, limit=limit), ingest_graph,
        # (issue 0028 / ADR-0091: the KG-7 PartyTo link step was retired; no link_fn is wired.)
        # RESUME-skip: a present Contract node means the whole document already landed (Contract is written last).
        is_done=lambda doc: ContractKGStore(store).contract_by_id(doc.source_doc_id) is not None)
