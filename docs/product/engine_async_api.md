# Engine async API — the interface contract for the product

> Status: finalized by ASYNC-D2 (ADR-0057). This is the contract the **product** (RuleWright, a separate
> closed repo, ADR-0052) depends on. Dependency direction is strict and one-way: **Product → Engine, never
> Engine → Product**. When an entrypoint's signature changes, update this doc in the same commit.

## The one rule

**Every engine entrypoint that does model work is `async`.** The engine is async end to end (ADR-0057) so that
every model call runs under a **true wall-clock deadline** (`_MODEL_DEADLINE_S = 180 s` per logical call in
`models/seam.py`; streaming idle-drip bound `_STREAM_CHUNK_TIMEOUT_S = 60 s`; the compliance judge adds a
per-pair `RAG_JUDGE_TIMEOUT_S = 90 s` bound). This is the fix for engine issue 0003 (a synchronous pipeline +
per-socket timeout is not a wall-clock bound; a slow-drip response ran 591 s against a 60 s timeout).

Consequences for the caller:

- Call the `async def` entrypoints with `await` (inside an `async def`), or `asyncio.run(...)` at a process/CLI
  boundary. FastAPI/ASGI routes should be `async def` and `await` them directly (no `run_in_threadpool` hop).
- The `production_*` factories return a **compiled LangGraph** whose model-calling nodes are async. Call it with
  **`await graph.ainvoke(state)`**. Never `graph.invoke(...)` — a sync `.invoke` on an async-node graph raises.
- There is **no sync shim** and no sync fallback (ADR-0057, by decision). Do not reintroduce one.

## Query side (per-request, returns a result)

The query legs are exposed two ways: as **in-process LangGraph subgraphs** (call `.ainvoke`) and as **MCP tool
servers** (the product's Tier-1 tool surface). Both are async.

| Capability (ARD name) | In-process factory (→ `await graph.ainvoke(...)`) | MCP server module |
|---|---|---|
| `intra_document_qa` | `subgraphs.intra_document_qa.production_intra_document_qa(store=, answer_model_id=, ...)` | `mcp.intra_document_qa_server` |
| `relational_qa` | `subgraphs.relational_qa.production_relational_qa(store=, answer_model=)` | `mcp.relational_qa_server` |
| `typed_property_retrieval` | `subgraphs.typed_property_retrieval.production_typed_property_retrieval(store=, embedder=, extract_model=, ...)` | `mcp.typed_property_retrieval_server` |
| `compliance_check` | see the two `run_*` coroutines below | `mcp.compliance_server` |

Compliance verdict coroutines (await directly; return a cited `ComplianceReport`):

```python
report = await run_compliance_check(          # advertising-tuned (FTC 16 CFR 255)
    subject_text, source_doc, *, store, extract_model, judge_model_id, embedder=None, k=5)

report = await run_generic_compliance_verdict( # domain-agnostic (any ingested regulation)
    subject_text, source_doc, *, store, judge_model_id, embedder, k=8)
```

**MCP servers** — each module exposes `build_<name>_mcp(fn)` (inject a stub for tests) + `production_*_fn(...)`
(env-wired) and a `main()` that serves over stdio (`RAG_MCP_DEMO=1` for the no-infra demo). The `@mcp.tool`
handlers are `async def` awaiting the async subgraph (FastMCP awaits coroutine tool handlers natively).

## Ingestion side (build/extend a knowledge graph)

Blocking (await to completion, returns an `IngestionReport`):

```python
await arun_cuad_ingestion(cuad_path, store, *, cache_dir, limit=0)                 # corpus.cuad_ingestion
await arun_corpus_ingestion(adapter, ingest_graph, *, is_done=None, ...)           # subgraphs.contract_ingestion_pipeline
await run_compliance_ingestion(sections_path, store, *, model, source=..., ...)    # subgraphs.compliance_ingestion
await run_compliance_document_ingestion(doc_name, data, store, *, model, source, ...)  # customer PDF/DOCX policy
await run_requirement_extraction(text, *, model, source, section, ...)             # one regulation section
```

Fire-and-forget async **jobs** (return a `job_id` **synchronously**, ingest in the background with bounded
parallelism; poll `jobs.get(job_id)` for status + dead-letters):

```python
job_id = submit_ingestion(adapter, ingest_graph, jobs, *, job_id, db, corpus_ref, ...)      # subgraphs.async_ingestion
job_id = submit_compliance_ingestion(adapter, store, jobs, *, job_id, model, source, ...)    # subgraphs.compliance_ingestion
```

`submit_*` are the only ingest entrypoints that return without `await` — they intentionally hand the work to a
background async runner (`run_job` → `await ingest_graph.ainvoke(...)`) so the caller (e.g. a product API route)
returns immediately. `JobStore` writes are atomic (write-temp-then-`os.replace`) so a poll never reads a partial
job record.

### Reading an `IngestionReport` — do not miss a partial loss

A document can be written but **incomplete**: a clause extraction failed after retries, *or* a span-index write
failed (best-effort index; the doc is never dead-lettered for it). Both are reported in `report.partial`, one
entry per affected document:

```python
for p in report.partial:                       # p["source_doc_id"]
    for f in p["failures"]:                     # ALWAYS present; covers EVERY loss kind
        where = f.get("span_id") or f.get("page")   # kind-specific detail (span_id, page, ...) -- don't assume one
        log(f'{p["source_doc_id"]} lost {f["kind"]} {where}: {f["reason"]}')
```

**Key on `failures` (or simply on the document appearing in `partial`).** The per-kind keys `clause_failures`
and `span_failures` are **optional** — each is present only when that kind of loss occurred — so reading only
`clause_failures` **silently misses a span-only loss** (a document that dropped only spans still ingested and
looks clean). `failures` is the always-present, kind-tagged (`{"kind": "clause"|"span"|"ocr", ...}`; new kinds may be added)
union; the per-kind keys remain for back-compat. `report.per_document` lists what was written; `partial` and
`dead_lettered` list what was not — join by `source_doc_id`.

**Build partial entries through the engine, don't re-derive them.** If your worker owns the per-document loop
(e.g. it drives the compiled graph itself rather than calling `arun_corpus_ingestion`), assemble each partial
entry with the engine helper instead of comparing loss keys yourself — one definition, no drift:

```python
from rag_wright.subgraphs.contract_ingestion_pipeline import build_partial_entry   # STABLE public helper

entry = build_partial_entry(doc_id, out.get("clause_failures"), out.get("span_failures"))  # None if complete
if entry is not None:
    partial.append(entry)
```

`build_partial_entry(source_doc_id, clause_failures, span_failures, ocr_failures=None) -> dict | None` is
**stable public API**: this import path and the `failures` shape are pinned by a contract test. **Forward-compat:**
a new loss kind arrives as a new `kind` value inside `failures` (never a replacement top-level key) and, if the
producer needs it, a new *optional trailing* arg — so a 3-positional caller is unaffected, and counting the
kind-tagged list (rather than summing known per-kind fields) keeps surfacing loss kinds your code predates.
`ocr` (a page a degraded scan left unreadable, 0009) is the first kind added this way.

**Parse a document ASYNC — don't hand-roll the wrapper.** A hand-built ingest needs the *structure-bearing*
`SourceDocument` (carries `.parsed` for the chunker, `.text`, and `.ocr_unreadable_pages`), which
`parsed_source_document` builds. Its async, deadline-bounded twin is:

```python
from rag_wright.subgraphs.contract_ingestion_pipeline import aparsed_source_document

sd = await aparsed_source_document(source_doc_id, name, data, *, cache_dir, metadata=None, deadline_s=600)
```

It runs the sync build (docling parse + the tiered OCR/VLM escalation — the slowest call) OFF the event loop via
`to_thread` under an `asyncio.timeout`, so it never blocks the loop. Use this instead of writing your own
`to_thread(parsed_source_document, …)` wrapper — one engine definition, no drift in the deadline, the OCR
escalation, or the `.ocr.json` caching. (Note `aparse_document_bytes` returns a raw `DoclingDocument`, NOT a
`SourceDocument` — use `aparsed_source_document` when your pipeline needs the `SourceDocument`.) Same `to_thread`
caveat as the engine's own async parse: the deadline unblocks the caller; the docling worker thread finishes in
the background.

## ARD / discovery

Every capability is registered under its FR-C name (`capabilities/registry.py`, 52 registrations) for Agentic
Resource Discovery (`urn:air`, ADR-0052 — a standing commitment independent of the parked GraphWright compiler).
The Tier-1 product tool surface is the four MCP servers above (`compliance_check`, `intra_document_qa`,
`relational_qa`, `typed_property_retrieval`); each also has a distinct `*_mcp` ARD identity (`kind="mcp_tool"`)
from its in-process subgraph identity (`kind="subgraph"`), same output contract.

## What the product must NOT do

- Do not call `.invoke(...)` on a `production_*` graph (raises — use `.ainvoke`).
- Do not wrap an engine coroutine in a thread to "make it sync" — await it; the deadline lives on the event loop.
- Do not import from the product into the engine (CI enforces Product → Engine via an import-linter rule).

## Related

- ADR-0057 — async engine architecture (the deadline model).
- ADR-0052 — engine/product split; GraphWright parked; ARD standing.
- ADR-0050 — async LangGraph ingestion (the job/dead-letter model).
- `docs/archive/plans/async-migration.md` — the phased migration record.
