# API reference — `rag_wright.api`

> **Generated** from the live `rag_wright.api.__all__` by `scripts/build_api_docs.py` — do not edit by hand. Regenerate with `bash scripts/build_api_docs.sh`; CI diffs it, so it cannot drift. The whole public surface is imported from `rag_wright.api`.

## Types

### `EngineConfig(store: 'StoreConfig', models: 'dict[str, str]' = <factory>, embeddings: 'dict[str, str]' = <factory>, options: 'EngineOptions' = <factory>, pack: 'Optional[str]' = None) -> None`

The product's view of the engine: the store connection, chosen models (by role alias), the embedding profile, and the `options` catalog. Defaults just work; override only to trade quality/cost/latency. Implementation details (ArcadeDB, BGE) never cross this boundary.

### `StoreConfig(host: 'str', port: 'str', user: 'str', password: 'str', backend: 'str' = 'arcadedb', protocol: 'str' = 'http') -> None`

How to reach the KG/retrieval store. `backend` selects the implementation (only `arcadedb` today; a new backend -- e.g. Neo4j -- is added here, invisibly to the product).

### `EngineOptions(ingest: 'IngestOptions' = <factory>) -> None`

The engine's options catalog. Ingest knobs today; retrieval / reranking / chunking groups are added here as they are promoted off environment variables.

### `IngestOptions(classify_concurrency: 'Optional[int]' = None, clause_concurrency: 'Optional[int]' = None, affiliations: 'Optional[bool]' = None, function_classifier: 'Optional[str]' = None, list_model: 'Optional[str]' = None, clause_samples: 'Optional[int]' = None) -> None`

Ingest-time knobs, settable through config instead of environment variables (EP-API-4a). Every field defaults to `None` = "use the engine default", so the engine's existing env fallback is preserved (a non-API caller is unaffected) and an API caller that leaves these unset gets today's behavior exactly. Set a field to override.

### `WorkspaceHandle(store: 'Any', config: 'EngineConfig', corpus: 'str') -> 'None'`

An opaque handle to a resolved engine workspace. Public surface: `model_id(role)`. The resolved store + embedder are engine-internal (`_store` / `_embedder`), used by the invokers -- NOT a product accessor.

### `Discovered(slug: 'str', kind: 'str', description: 'str', representative_queries: 'tuple[str, ...]', score: 'float') -> None`

One ranked capability match from `discover` — enough for an agent to pick and invoke it by `slug`.

### `KgNode(type: 'str', key_field: 'str', props: 'dict[str, object]') -> None`

A typed KG node to upsert (DD-1b, ADR-0117): `type` is the vertex type, `key_field` the identity field to upsert on, `props` the fields (including `key_field`) as DOMAIN-NATIVE values. The store encodes each prop by its pack-declared storage type -- the caller never serializes to the store's wire format.

### `KgEdge(type: 'str', from_type: 'str', from_key_field: 'str', from_key: 'object', to_type: 'str', to_key_field: 'str', to_key: 'object', props: 'dict[str, object]') -> None`

A typed KG edge to create between two nodes identified by (type, key_field, key). `props` are native values (edge properties are type-driven: edges declare no storage schema).

### `UsageTotals(calls: 'int' = 0, input_tokens: 'int' = 0, output_tokens: 'int' = 0, cost_usd: 'float' = 0.0, calls_without_cost: 'int' = 0, latency_ms_total: 'float' = 0.0, by_model: 'dict[str, ModelUsage]' = <factory>, _lock: 'threading.Lock' = <factory>) -> None`

The usage accumulated within one `usage_scope()`: top-level totals + a per-model breakdown.

### `ModelUsage(calls: 'int' = 0, input_tokens: 'int' = 0, output_tokens: 'int' = 0, cost_usd: 'float' = 0.0, calls_without_cost: 'int' = 0, latency_ms_total: 'float' = 0.0) -> None`

Per-model totals within a scope (`cost_usd` sums KNOWN per-call costs only).

### `LayoutItem(*, kind: Literal['title', 'heading', 'paragraph', 'list_item', 'table', 'caption', 'footnote', 'page_header', 'page_footer', 'code', 'formula', 'form', 'other'], text: str, start: int, end: int, level: Optional[int] = None, pages: list[int] = []) -> None`

One layout element of the parsed document that overlaps a chunk, in CHUNK-relative offsets.

### `Span(*, span_id: str, parent_chunk_id: str, parent_okf_path: str = '', span_index: int, start: int, end: int, text: str, pages: list[int] = [], bbox: tuple[float, float, float, float] | None = None, kind: Optional[Literal['title', 'heading', 'paragraph', 'list_item', 'table', 'table_row', 'caption', 'footnote', 'page_header', 'page_footer', 'code', 'formula', 'form', 'other']] = None) -> None`

One span: the smallest citeable unit, indexed for retrieval. It points back to its parent chunk; `span_id` is `<parent_chunk_id>#<span_index>` (identifier rule) and `start`/`end` are offsets into the chunk text.

### `TaggedSpan(*, span: rag_wright.contracts.ingestion.Span, tags: list[str] = [], scores: dict[str, float] = {}) -> None`

A span with the optional span tagger's soft tags (primary first) and their scores.

### `Unit(*, index: int, anchor: rag_wright.contracts.ingestion.Span, spans: list[rag_wright.contracts.ingestion.Span], text: str, tags: list[str] = []) -> None`

The extraction unit: consecutive spans grouped by the unit grouper. `text` is what the extractor reads; `anchor` is the citation anchor for records read from it (a member span).

### `UnitExtraction(*, nodes: list[rag_wright.store.seam.KgNode] = [], edges: list[rag_wright.store.seam.KgEdge] = []) -> None`

What an extractor returns for one unit: typed KG nodes/edges in the pack's schema. Every node carries `span_id` (a span of the unit) and `confidence` (a `ConfidenceTag` value) as props (FR-S.4).

### `IngestionContractError`

A hook's output broke the ingestion contract (tiling, unit integrity, or record provenance).

## Hook protocols

Callables you pass to the engine; any function with this signature conforms.

### `Segmenter: (chunk_id: 'str', text: 'str', layout: 'Sequence[LayoutItem]') -> 'list[Span]'`

chunk -> spans that tile its text. Sync (CPU). `layout` is the parse's layout overlapping the chunk (empty for a text-only source).

### `SpanTagger: (chunk_text: 'str', spans: 'Sequence[Span]') -> 'Awaitable[list[TaggedSpan]]'`

Optional: soft tags per span of one chunk, aligned to `spans`.

### `UnitGrouper: (spans: 'Sequence[TaggedSpan]', *, decider: 'Optional[BoundaryDecider]' = None) -> 'Awaitable[list[Unit]]'`

A document's spans (in order, across chunks) -> extraction units. May drop spans (e.g. page furniture); a dropped span stays in the span index. `decider`, when set, adjudicates boundaries the grouper is unsure of.

### `Extractor: (unit: 'Unit', *, source_doc_id: 'str') -> 'Awaitable[UnitExtraction]'`

One unit -> the domain's typed records (required; the domain-specific step).

### `RecordWriter: (source_doc_id: 'str', extractions: 'Sequence[UnitExtraction]') -> 'Awaitable[None]'`

Optional: persist a document's extractions. The engine default writes them with `kg_write`.

## Type aliases

### `LayoutKind = typing.Literal['title', 'heading', 'paragraph', 'list_item', 'table', 'caption', 'footnote', 'page_header', 'page_footer', 'code', 'formula', 'form', 'other']`

### `SpanKind = typing.Literal['title', 'heading', 'paragraph', 'list_item', 'table', 'table_row', 'caption', 'footnote', 'page_header', 'page_footer', 'code', 'formula', 'form', 'other']`

### `BoundaryDecider = typing.Callable[[list[str]], typing.Awaitable[list[bool]]]`

## Functions

### `open_workspace(config: 'EngineConfig', *, corpus: 'str', reset: 'bool' = False) -> 'WorkspaceHandle'`

Resolve (and cache) the workspace for `corpus` (the backend database name) from `config`. Ensures the schema. Returns an opaque `WorkspaceHandle`. `reset=True` drops + recreates the database (test/clean-slate) and bypasses the cache.

### `ainvoke_subgraph(name: 'str', inputs: 'dict', *, resources: 'WorkspaceHandle') -> 'Any'`

Invoke a subgraph-kind capability by name over the workspace, inside a trace span. The implementation is resolved lazily from the manifest `impl_ref` (no central adapter dict). Retry/dead-letter comes from the LangGraph scaffold the subgraph is built on; usage is captured by the caller's `measure_usage()` (EP-API-5).

### `invoke_model(name: 'str', inputs: 'dict', *, resources: 'WorkspaceHandle') -> 'Any'`

Invoke a model-kind capability by name (SYNCHRONOUSLY). Validated against the ARD catalog, then resolved via `impl_ref` and dispatched -- the SAME path the ingestion pipeline routes through (one production path, no second hand-built fleet). `resources` is accepted for API uniformity but model capabilities are store-independent. Usage is the caller's `measure_usage()` scope (EP-API-5). A model impl may be async (I/O-bound, e.g. an LLM-backed cap) -- those cannot be invoked here; call `ainvoke_model` instead (we refuse rather than silently return an un-awaited coroutine).

### `ainvoke_model(name: 'str', inputs: 'dict', *, resources: 'WorkspaceHandle', sem: 'asyncio.Semaphore | None' = None) -> 'Any'`

Invoke a model-kind capability by name, ASYNCHRONOUSLY -- the async surface for model caps (the subgraph legs already have `ainvoke_subgraph`). A model impl is one of two shapes, and this routes each honestly: * SYNC (CPU-bound local inference -- a classifier/XGBoost fleet): run OFF the event loop in a worker thread (`asyncio.to_thread`), so a big batch never blocks the loop; * ASYNC (I/O-bound -- an LLM-backed cap calling OpenRouter or a local vLLM client): AWAITED directly, so the I/O concurrency is real (not a thread wrapping a blocking call). `sem` (an `asyncio.Semaphore`) bounds total in-flight work when a caller fans out a batch -- the same backpressure the ingestion pipeline applies via `adispatch_model`. Usage is the caller's `measure_usage()` scope (EP-API-5).

### `capability_index() -> 'dict[str, dict]'`

Public discovery index: `{slug: {kind, description}}` for every catalogued capability (the 'cards').

### `discover(query: 'str', *, resources: 'WorkspaceHandle', kind: 'Optional[str]' = None, k: 'int' = 8) -> 'list[Discovered]'`

Rank the live ARD catalog by semantic match to `query`; return the top `k` (optionally filtered to one `kind`: `subgraph` / `model` / `function` / `agent_skill` / `mcp_tool`). Embedding-based, via the workspace's query embedder (BGE-M3, the same space retrieval uses). Returns `[]` when the (filtered) catalog is empty; raises `RuntimeError` if the workspace has no query embedder available (discovery needs one).

### `kg_read(ws: 'WorkspaceHandle', node_type: 'str', *, where: 'Optional[dict]' = None, fields: 'Optional[list]' = None, distinct: 'Optional[str]' = None, order_by: 'Optional[str]' = None, limit: 'Optional[int]' = None) -> 'list[dict]'`

Read typed nodes of `node_type` from the workspace (see `Store.kg_read`). Equality/`IN` filters, projection, distinct, order, limit; an empty list `where` value is scope-to-nothing -> `[]`.

### `kg_write(ws: 'WorkspaceHandle', nodes: 'list', edges: 'Any' = ()) -> 'None'`

Upsert typed `nodes` + create typed `edges` in one transaction (see `Store.kg_write`). `nodes`/`edges` are `KgNode`/`KgEdge` (from `rag_wright.store.seam`); the store encodes each field per its pack-declared type.

### `kg_edges(ws: 'WorkspaceHandle', from_type: 'Optional[str]' = None, *, where: 'Optional[dict]' = None, key_range: 'Optional[tuple]' = None, direction: 'str' = 'out', edge_type: 'Optional[str]' = None, edge_where: 'Optional[dict]' = None, target_where: 'Optional[dict]' = None, select: 'dict') -> 'list[dict]'`

Generic edge TRAVERSAL over the workspace (see `Store.kg_edges`): node-start out/in MATCH (by `where` equality/membership or a contract-scope `key_range`) or a direct edge scan; `select` projects `c.`/`e.`/`v.` expressions. The engine's relational/graph primitive on the API, so a domain's graph query never touches `ws._store`. (`NOT_NULL` for a presence filter is `rag_wright.store.seam.NOT_NULL`.)

### `entities_by_name(ws: 'WorkspaceHandle', name: 'str') -> 'list[dict]'`

Resolve an entity NAME to every entity node it matches: `[{entity_id, name, entity_type}]` (the engine owns the surface-form normalization, so variants collapse to one id). One name can match several nodes (a resolved node + an unlinked ref sharing a clustering key) -- all are returned. `entity_id` is exactly the `start_entity_id` a graph traversal takes. The engine's generic entity-lookup primitive on the API.

### `span_positions(ws: 'WorkspaceHandle', document: 'str') -> 'list[dict]'`

Every span of `document` with its position provenance (doc offsets, pages, DECODED bbox), ordered by document position. The engine MECHANISM behind a product's citation/highlight types -- the product wraps these rows into its own presentation type (e.g. `SpanLocation`).

### `document_of(entity_id: 'str') -> 'str'`

The source-document id embedded in a span/chunk/clause id (`<source_doc_id>:<idx>:<hash>` -> the first, delimiter-safe segment). Empty in -> empty out.

### `id_source(requirement_id: 'str') -> 'str'`

The source/policy of a Requirement id (`<source>:<section>:<hash>` -> the first segment; `source` is delimiter-safe via `canonical_source_doc_id`, so this is the same first-segment rule as `document_of`).

### `decode_bbox(raw: 'Optional[str]') -> 'Optional[tuple]'`

Decode the engine's best-effort bounding box (stored as a JSON `[l,t,r,b]` string) to a `(l, t, r, b)` tuple, or None. The single canonical decoder (retires the product seam's copy).

### `source_document(document_id: 'str', *, text: 'str') -> 'Any'`

A text-only `SourceDocument` (`source_doc_id`, `text`) to pass as the `document` input of the `contract_ingestion_pipeline` capability. The id should be a canonical, delimiter-safe source-doc id.

### `parse_document(document_id: 'str', path: 'Any', *, cache_dir: 'Any', metadata: 'dict | None' = None) -> 'Any'`

Docling-parse the file at `path` ONCE (content-hash gated + cached under `cache_dir`) into a structure-bearing `SourceDocument` -- `.parsed` carries the `DoclingDocument` so the chunker's structural pass fires on real headings, and `.text` holds the flattened text. This is the PDF/DOCX/HTML/MD ingest entry point of the engine API; pass the result as the `document` input of `contract_ingestion_pipeline`. The docling parse blocks; use `aparse_document` on an event loop.

### `aparse_document(document_id: 'str', path: 'Any', *, cache_dir: 'Any', metadata: 'dict | None' = None) -> 'Any'`

The async, deadline-bounded twin of `parse_document` (ADR-0057): runs the docling parse off the event loop so a hand-built async ingest can parse a document into a structure-bearing `SourceDocument` without blocking.

### `measure_usage() -> 'Iterator[UsageTotals]'`

Accumulate the model usage of every engine call made inside the block; read the returned `UsageTotals` after it. Nesting is additive, so a task-level scope totals everything while an inner per-call scope attributes its slice. Capturing is opt-in: with no active scope, the engine records usage nowhere (zero overhead).

### `register_capability(manifest: 'CapabilityManifest') -> 'None'`

Register (or replace) one capability in the runtime ARD catalog. A product calls this for each of its domain capabilities (with an `impl_ref`); the invoker then resolves it by name with zero engine edits.

### `load_reference_pack() -> 'None'`

Register the engine's reference pack into the runtime catalog -- the opt-in worked example (the engine's own test suite loads it; a downstream product does NOT, registering its own capabilities instead).

### `reference_pack() -> 'tuple[CapabilityManifest, ...]'`

The engine's committed REFERENCE PACK manifests (the contract/compliance worked example). Opt-in.

### `check_tiling(chunk_id: 'str', text: 'str', spans: 'Sequence[Span]') -> 'None'`

A segmenter's spans must tile `text` in order, byte-faithfully, under the `span_id` scheme.

### `check_units(spans: 'Sequence[Span]', units: 'Sequence[Unit]') -> 'None'`

A grouper's units must use known spans, each at most once, in document order, indexed 0..k-1.

### `check_extraction(unit: 'Unit', extraction: 'UnitExtraction') -> 'None'`

Every record node must cite a span of its unit and carry a `ConfidenceTag` (FR-S.4).
