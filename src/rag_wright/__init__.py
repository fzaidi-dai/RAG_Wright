"""RAG_Wright: the open-core RAG ENGINE (RAG_Capability_Spec.md v0.1; engine/product split, ADR-0052).

This package builds and registers the FR-C capabilities (parsing, chunking, embedding, hybrid search,
reranking, graph extraction, entity resolution, ontology and registry derivation, reasoning and generation,
and the RLM skill) as ordinary tested software, AND the ingestion and query pipelines that compose them --
ordinary hardened LangGraph subgraphs, not a compiler output (GraphWright is PARKED, ADR-0052). Each capability
is registered under its FR-C name for Agentic Resource Discovery (ARD, `urn:air`).

The engine is ASYNC end to end (ADR-0057): every model call runs under a true wall-clock deadline. The public
ingest / query / compliance entrypoints are `async` (or return LangGraph graphs called via `.ainvoke`); see
`docs/product/engine_async_api.md` for the interface contract the product (RuleWright) depends on. Dependency
direction is strict and one-way: Product -> Engine, never Engine -> Product.
"""
