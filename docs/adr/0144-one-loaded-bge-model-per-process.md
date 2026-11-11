# ADR-0144: One loaded BGE model per process, with its one lock

**Status:** accepted · **Date:** 2026-11-11 · **Related:** PS-65, TexWright's request
`2026-11-09-the-embedder-model-is-rebuilt-for-every-workspace.md`, issue 0016 (in-place mutation on encode), PS-52
(the workspace embedder's lazy build), PS-55 / ADR-0140 (`METAL_LOCK`)

## Context

Every `BGEM3Embedder` and `BGEReranker` loaded its own copy of its model: each workspace's query embedder, each
ingestion pipeline's embedder, and the reference pack's `intra_document_qa`, which builds a reranker per call. Nothing
chose this; PS-52's lock only makes one handle's lazy build atomic. Live, one process holding three workspaces, an
ingestion pipeline and two rerankers peaked at 2816 MB, each repeat costing 1 to 1.5 s. The lock a wrapper holds
was per instance, so two wrappers of one shared model would each lock their own lock and run the model together,
which issue 0016 shows segfaults.

## Decision

- `rag_wright.util.devices.shared_model(key, device, build)` keeps one loaded model per key -- (kind, model name,
  precision, device) -- for the life of the process, built once under a guard, and returns it WITH its one lock
  (`model_lock`: `METAL_LOCK` on Metal, else a lock of its own). Both BGE wrappers take their model and lock from it,
  so every wrapper of a model serializes on the same lock. An injected `model=` is not shared.
- The wrappers stay per workspace; only the model and its lock are shared.

## Consequences

- Live, the same process peaks at 1534 MB and every repeat is free; the encodes through two wrappers from 8 threads
  equal the serial vectors.
- Off Metal, two tenants' encodes on one model now queue on its lock where each had its own model before. Encoding is
  a small slice of an ingest and Metal already serialized everything, so memory and load time win. A tenant that needs
  its own model runs in its own process.
- A loaded model is never freed before the process exits; dropping a workspace does not release it.
