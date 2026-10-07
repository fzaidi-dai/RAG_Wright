"""EP-REF-1d: a REFERENCE product seam for the engine's CONTRACT/COMPLIANCE reference domain -- a worked example
of what a product seam looks like AFTER the domain-agnostic separation. It is the shape EP-SEAM-3 refactors
RuleWright's real `engine/seam.py` toward.

Every method is a thin composition over the engine: `open_workspace` (tenancy = one call), the invokers
(`ainvoke_subgraph`), the generic API reads (`entities_by_name`), and the reference-pack store extensions
(`ContractKGStore` / `ComplianceStore`) + the compliance invoker wrappers. It holds NO `ArcadeDBStore`, embedder,
model id, or id-string parsing -- those are engine calls.

This is a worked EXAMPLE, deliberately thin. A real product adds, around these calls, the concerns marked
`# PRODUCT OWNS:` below -- tenancy policy, scoping (`ScopeViolation`), the FTC/ad-compliance variants, the
unknown-policy guard, presentation/citation types, caching, and routing the engine's usage/progress to its own
telemetry. Those stay product-side; see `docs/product/seam-adaptation-guide.md`.
"""
from __future__ import annotations

from typing import Any, Optional

from rag_wright.api import (
    EngineConfig,
    ainvoke_subgraph,
    aparse_document,
    entities_by_name,
    open_workspace,
    source_document,
)
from rag_wright.packs.compliance.capabilities.compliance_store import ComplianceStore
from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore
from rag_wright.packs.compliance.invokers import invoke_compliance_check, invoke_policy_ingest


class ContractComplianceSeam:
    """A thin reference seam over the engine for the contract/compliance reference domain."""

    def __init__(self, config: EngineConfig) -> None:
        self._config = config

    # --- tenancy -------------------------------------------------------------------------------------
    def open(self, corpus: str, *, reset: bool = False):
        """Open a workspace for a corpus (one engine call). PRODUCT OWNS: per-tenant corpus selection + auth policy."""
        return open_workspace(self._config, corpus=corpus, reset=reset)

    # --- ingestion + query (compose the invokers) ----------------------------------------------------
    async def ingest_contract(self, ws, doc_id: str, *, cache_dir: str, text: Optional[str] = None,
                              path: Any = None, metadata: Optional[dict] = None):
        """Ingest one contract: build a `SourceDocument` (docling-parse a file PATH, or wrap TEXT) then run the
        ingestion capability. Returns the engine's `IngestionReport`. PRODUCT OWNS: the document source + progress UI
        (the engine emits progress/usage; route it to your telemetry)."""
        sd = (await aparse_document(doc_id, path, cache_dir=cache_dir, metadata=metadata) if path is not None
              else source_document(doc_id, text=text or ""))
        return await ainvoke_subgraph("contract_ingestion_pipeline",
                                      {"document": sd, "cache_dir": cache_dir}, resources=ws)

    async def ask_contract(self, ws, contract_id: str, question: str):
        """Single-document Q&A over one contract. Returns the engine's cited answer (raw -- PRODUCT OWNS: what
        counts as 'answered' / how to render the citations)."""
        return await ainvoke_subgraph("intra_document_qa",
                                      {"contract_id": contract_id, "question": question}, resources=ws)

    async def search_corpus(self, ws, query: str, *, k: int = 8, documents: Optional[list[str]] = None):
        """Corpus-wide typed/similarity retrieval (Leg B). The leg extracts the query's typed constraints itself;
        pass `documents` to scope to a workspace's docs. PRODUCT OWNS: ranking/selection presentation."""
        return await ainvoke_subgraph("typed_property_retrieval",
                                      {"query": query, "k": k, "documents": documents}, resources=ws)

    # --- relational / terms / citations (reference-pack store extensions over the workspace store) ----
    def find_party(self, ws, name: str) -> list[tuple[str, str]]:
        """A party NAME -> every `(entity_id, stored_name)` it resolves to (engine-normalized; one name can match
        several nodes -- all are returned, never the first only)."""
        return sorted((str(e["entity_id"]), str(e.get("name") or "")) for e in entities_by_name(ws, name))

    def counterparties(self, ws, entity_id: str, *, max_hops: int = 1, documents: Optional[list[str]] = None):
        """The parties this one has a CONTRACTS_WITH edge to (one hop by default)."""
        return ContractKGStore(ws._store).party_counterparties(entity_id, max_hops=max_hops, documents=documents)

    def affiliates(self, ws, entity_id: str, *, documents: Optional[list[str]] = None):
        """The parties this one has an AFFILIATE_OF edge to (same corporate group -- a separate traversal)."""
        return ContractKGStore(ws._store).party_affiliates(entity_id, documents=documents)

    def contract_terms(self, ws, contract_id: str) -> list:
        """The typed clauses of one contract (the full view -- keeps AMBIGUOUS out-of-vocab values)."""
        return ContractKGStore(ws._store).contract_terms(contract_id)

    def span_locations(self, ws, contract_id: str) -> list:
        """Every span's position (pages/bbox/offsets) + the clause ids on it -- for a citation preview."""
        return ContractKGStore(ws._store).span_locations(contract_id)

    @staticmethod
    def canonical_clause_type(label: str) -> Optional[str]:
        """Map a user's clause label onto the taxonomy (alias-resolving), or None -- to validate a correction."""
        return ContractKGStore.canonical_clause_type(label)

    @staticmethod
    def clause_type_vocabulary() -> tuple[str, ...]:
        """Every clause type a sweep/correction UI can be scoped to."""
        return ContractKGStore.clause_type_vocabulary()

    # --- compliance (reference invoker wrappers + the read facade) -----------------------------------
    async def ingest_policy(self, ws, *, source: str, sections_path: Any = None,
                            doc_name: Optional[str] = None, data: Optional[bytes] = None):
        """Ingest a policy into the Requirement KG (sections.json OR a document's bytes). Returns an
        `IngestionReport`. PRODUCT OWNS: the unknown-policy guard / curation workflow."""
        return await invoke_policy_ingest(ws, source=source, sections_path=sections_path,
                                          doc_name=doc_name, data=data)

    async def check(self, ws, *, subject_text: str, source_doc: str, k: int = 8,
                    sources: Optional[list[str]] = None):
        """Check a subject against the Requirement KG -> a `ComplianceReport` (the GENERIC verdict). PRODUCT OWNS:
        the FTC/ad-compliance tuned variant (`run_ad_compliance_check`), the unknown-policy guard, the human gate."""
        return await invoke_compliance_check(ws, subject_text=subject_text, source_doc=source_doc,
                                             k=k, sources=sources)

    def requirements_for(self, ws, source: str) -> list[dict]:
        """The curated requirement rows under one policy (the proof an ingest landed)."""
        return ComplianceStore(ws._store).requirements_for(source)

    def requirement_locations(self, ws, source: str) -> list:
        """Where each requirement of one policy sits in its document (pages + bbox) -- for a citation preview."""
        return ComplianceStore(ws._store).requirement_locations(source)

    def curated_requirement_count(self, ws, sources: Optional[list[str]] = None) -> int:
        """How many requirements are in scope -- the denominator of an honest coverage statement."""
        return ComplianceStore(ws._store).curated_requirement_count(sources)
