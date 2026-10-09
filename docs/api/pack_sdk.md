# Pack SDK reference — `rag_wright.pack_sdk`

> **Generated** from the live `rag_wright.pack_sdk.__all__` by `scripts/build_api_docs.py` — do not edit by hand. The pack-author tier: what a domain pack's code needs beyond `rag_wright.api` (a pack imports only these two), with the same compatibility promise. See the module docstring for what belongs here.

## Types

### `ChunkId(*, source_doc_id: str, chunk_index: int, content_hash: str) -> None`

The stable identifier for a chunk (FR-S.2).

Scheme: source-document identifier, chunk index, and a content hash of the chunk text. The content hash is what makes the identifier change when (and only when) the chunk content changes, so an unchanged document re-chunks to the same ids (the content-hash gate in FR-I.1 and FR-I.5 relies on this).

### `EntityId(*, value: str) -> None`

The canonical entity identifier (FR-S.3): an opaque canonical-registry id string.

The engine is domain-agnostic (DD-4, ADR-0067/0117), so the FORMAT of a canonical id is owned by the resolver / domain pack, NOT by this contract. The SEC pack resolves to a 10-digit zero-padded EDGAR Central Index Key (CIK), e.g. ``"0000320193"`` (shaped in ``packs/contracts/corpus/edgar.normalize_cik``); a generic pack uses an exact-normalized surface-form key; another domain uses its own scheme. This contract's only invariant is therefore the domain-neutral one: a non-empty string. That is still a real invariant -- every downstream holder of an `EntityId` can trust it is a present, non-blank id -- while the format check lives at the one boundary that knows the domain (the resolver/loader), where the world's mess actually arrives, per the "normalize at the boundary" rule.

### `ConfidenceTag(*values)`

The confidence a graph-derived fact carries (FR-S.4). A closed set, no other value.

- ``EXTRACTED``: read directly from a source chunk. - ``INFERRED``: derived by reasoning over one or more chunks, not stated verbatim. - ``AMBIGUOUS``: supported but with competing readings or unresolved mentions.

Members: `EXTRACTED` (`'EXTRACTED'`), `INFERRED` (`'INFERRED'`), `AMBIGUOUS` (`'AMBIGUOUS'`)

### `GraphFact(*, provenance: rag_wright.contracts.provenance.Provenance, confidence: rag_wright.contracts.provenance.ConfidenceTag) -> None`

The base for a graph-derived fact: it carries provenance and a confidence tag (FR-S.4).

Graph nodes and edges (FR-I.4) carry the originating `chunk_id` (through `provenance`) and a `confidence` tag. Graph extraction (T5) and graph storage (T24) extend this base with their own ontology-conforming fields.

### `Provenance(*, source_doc_id: str, chunk_id: rag_wright.contracts.identifiers.ChunkId) -> None`

The source document and chunk a stored text unit came from (FR-S.4).

The `chunk_id` (FR-S.2) already carries its source-document identifier; `source_doc_id` is kept as an explicit, denormalized field so a citation is self-describing, so records can be filtered and indexed by source document at the store level (metadata filters, FR-Q.1), and so downstream code never has to parse `chunk_id` to recover the document. The redundancy is safe only because the two fields cannot disagree: the `_source_matches_chunk` validator runs on every construction and deserialization path (raw constructor, `model_validate`, `model_validate_json`), and `Provenance.of(chunk_id)` derives `source_doc_id` from the chunk so callers cannot create an inconsistent one. Store deserialization must therefore use a validating path (`model_validate` / `model_validate_json`), not `model_construct`, which bypasses all validation.

### `EntityMention(*, text: str, entity_type: str, confidence: rag_wright.contracts.provenance.ConfidenceTag) -> None`

A pre-resolution entity mention: a surface form, its ontology type, and a confidence tag.

Graph extraction produces typed mentions (NER labels); entity resolution (FR-C.7 / T24) later maps the surface form to a canonical `entity_id` and creates the canonical `EntityNode`.

A mention IS an ontology-conforming graph fact (FR-S.4): it is read from the text, so a spaCy NER or model-extracted mention carries a `confidence` tag like any other fact (ADR-0012). Its `chunk_id` provenance is the containing `ExtractionResult.chunk_id` (mentions are anchored by the result, not individually provenanced, since resolution collapses many mentions to one node). This is deliberately how the spaCy path satisfies "each path produces facts carrying chunk_id + confidence" — by emitting confidence-bearing mentions, NOT by inventing edges: two entities that co-occur in a passage are often unrelated (a passing reference), so a proximity edge is a false-edge generator, and nothing downstream filters edges (T26 surfaces confidence, it does not gate on it — FR-C.5/FR-Q.3). A relationship comes from document structure a domain extractor reads, never from proximity (ADR-0012).

`text` here is the *same notion* as a `RelationshipFact`'s `source_ref` / `target_ref`: both are pre-resolution entity surface forms. Standalone mentions and relationship endpoints are two channels for the same entities, so entity resolution (T24) must resolve them as one mention stream; an entity appearing as both must resolve to a single node, not a duplicate.

### `ExtractionResult(*, chunk_id: rag_wright.contracts.identifiers.ChunkId, entity_mentions: list[rag_wright.contracts.extraction.EntityMention] = [], relationship_facts: list[rag_wright.contracts.graph.RelationshipFact] = []) -> None`

What one extractor produces for one chunk: ontology-conforming facts plus typed mentions.

Every fact is anchored to `chunk_id`: its provenance must point at this chunk, so the extraction result carries the originating `chunk_id` end to end (FR-I.4). Facts already conform to the ontology (their type fields are the T4 enums), so a non-ontology fact cannot be built at all.

### `EntityNode(*, entity_id: rag_wright.contracts.identifiers.EntityId, entity_type: str, name: str) -> None`

A canonical entity node in the graph skeleton (SPEC.md section 8): identifier, name, type.

No facts and no confidence: entity nodes are canonical (resolved against the registry, FR-C.7), not extracted facts. DD-5 (ADR-0066/0117): `entity_type` is an OPAQUE string the domain names -- the engine does not constrain the taxonomy. The reference contract pack's value set lives in `packs/contracts/ontology/contract_taxonomy.py` (e.g. "Organization"/"Person"); a new domain names its own.

### `RelationshipFact(*, provenance: rag_wright.contracts.provenance.Provenance, confidence: rag_wright.contracts.provenance.ConfidenceTag, source_ref: str, relationship_type: str, target_ref: str) -> None`

A directed entity-to-entity relationship extracted from a chunk (extends `GraphFact`: provenance + confidence).

The endpoints are pre-resolution entity mentions (surface forms), directed `source_ref -> target_ref`: source and target are distinct roles, not a symmetric pair, so T8 can add directed corporate-hierarchy relationship types without reopening this model. Entity resolution (FR-C.7 / T24) later maps each ref to a canonical `entity_id`.

`relationship_type` is an OPAQUE domain string (DD-5, ADR-0066/0117): the engine does not constrain the edge taxonomy; the caller (a domain graph) names it, and the reference contract pack's value set lives in `packs/contracts/ontology/contract_taxonomy.py` (e.g. "Contracts With"/"Affiliate Of"). The agreement a co-party fact derives from is its provenance's source document (`provenance.source_doc_id`); because every `GraphFact` requires provenance, that reference is always present, which makes shared-party multi-hop questions answerable from the graph.

Self-loop is rejected here only at the ref level (the same mention as both source and target). The post-resolution check (two *distinct* mentions that resolve to the same `entity_id`) belongs with entity resolution (T24), because two mentions can legitimately resolve to one entity.

### `ModelCallTimeout`

A logical model call exceeded the total wall-clock deadline (`_MODEL_DEADLINE_S`) and was truly cancelled (socket torn down). TERMINAL: a stalling peer is not a transient worth re-hitting, so this is deliberately NOT in `_STRUCTURED_RETRY_ON` and must be kept out of any pregel `retry_on` -- the caller degrades or dead-letters on it. It is the async fix a per-socket-op timeout cannot be (engine issue 0003 / ADR-0057).

### `TransientExtraction`

A retryable extraction blip. Custom (not in LangGraph's default no-retry list) -> DEFAULT_RETRY retries. (Generic: any extraction node raises it; moved here from the reference pack, ING-8b.)

### `ResolutionResult(*, entities: list[rag_wright.capabilities.entity_resolution.ResolvedEntity], relationships: list[rag_wright.capabilities.entity_resolution.ResolvedRelationship]) -> None`

The capability's output: resolved entities and relationships (self-loops removed).

### `EntityRules(*, role_terms: frozenset[str] = frozenset(), role_phrases: tuple[str, ...] = ()) -> None`

PS-R5b: a domain's own non-entity vocabulary, declared in its pack (ADR-0066) and passed to `is_entity` / `disambiguate` / `resolve_entities`. `role_terms`: names that are a role, not an entity, once normalized (a role such as "the applicant" or "the supplier"); `role_phrases`: phrases that mark a mention as a role description rather than a name (e.g. "together with", "collectively").

### `EntityRegistry(*, normalize: 'Optional[Callable[[str], str]]' = None) -> 'None'`

A closed-world registry of canonical entities, indexed by normalized surface form.

#### `EntityRegistry.add(self, record: 'RegistryRecord') -> 'None'`

_(no docstring)_

#### `EntityRegistry.get(self, entity_id: 'EntityId') -> 'Optional[RegistryRecord]'`

_(no docstring)_

#### `EntityRegistry.resolve(self, surface_form: 'str') -> 'Optional[EntityId]'`

The canonical `entity_id` for a known surface form, or `None` (closed-world).

### `RegistryRecord(*, entity_id: rag_wright.contracts.identifiers.EntityId, canonical_name: str, ticker: Optional[str] = None, aliases: list[str] = []) -> None`

One registered entity: its canonical id, conformed name, ticker, and known aliases.

### `GraphAnswer(*, start_entity_id: str, relationship_type: str, evidence: list[rag_wright.capabilities.graph_query.GraphEvidence]) -> None`

The graph query capability's output: candidate answers as evidence for fusion (FR-C.5, FR-Q.3).

### `BGEReranker(model_name: 'str' = 'BAAI/bge-reranker-v2-m3', *, use_fp16: 'bool' = False, model: 'object | None' = None) -> 'None'`

The real reranker: `FlagEmbedding.FlagAutoReranker` (model loaded lazily).

THREAD-SAFE (engine issue 0016): FlagEmbedding mutates the model in place on every call (the same in-place `.to()`/`.eval()` conversions that segfault the shared BGE-M3 embedder under thread concurrency), so a shared reranker is guarded by an instance lock too -- defense-in-depth for any concurrent `score` caller.

#### `BGEReranker.score(self, query: 'str', passages: 'list[str]') -> 'list[float]'`

_(no docstring)_

### `SourceDocument(*, source_doc_id: str, text: str, metadata: dict = {}, parsed: Optional[rag_wright.capabilities.parsing.ParsedDocument] = None, ocr_unreadable_pages: list[int] = [], skipped_hidden_sheets: list[str] = [], embedded: list[rag_wright.capabilities.document_parse.EmbeddedChild] = [], embedded_skipped: list[str] = []) -> None`

One document to ingest: its canonical `source_doc_id` (HYG-1), its already-parsed text, and optional per-corpus metadata (e.g. annotations or pre-segmented spans a corpus ships with) the stages may consult.

### `TieredOCRParser(*, fast: 'Parser | None' = None, vlm: 'Parser | None' = None) -> 'None'`

0009-WIRE: fast OCR -> scan-quality gate -> VLM escalation for degraded pages -> PARTIAL for what the VLM still cannot read. A `Parser`, so it drops into `parse(..., parser=TieredOCRParser())` unchanged.

Benchmark (docs/eval/ocr_benchmark.md): fast OCR is perfect on readable scans and worthless on a heavily degraded one (char_sim ~0.01); a VLM reads the degraded-but-readable scan (Gemma-4 0.991). So: run the cheap fast parse, and ONLY when a page's OCR is untrustworthy re-parse via the VLM (whole-document escalation -- the VLM reads good pages fine too, so this is safe and keeps the common readable case at zero VLM cost). `fast` and `vlm` are `Parser`s (injectable); the last run's `report` is exposed for PARTIAL reporting.

#### `TieredOCRParser.convert(self, source: 'Path') -> 'DoclingDocument'`

_(no docstring)_

### `StructuralModelFallbackDiscoverer(model_id: 'str | None' = None, *, token_cap: 'int' = 20000, structural: 'Any' = None, fallback: 'Any' = None, max_concurrency: 'int' = 8, guidance: 'Optional[str]' = None) -> 'None'`

CHUNK-4 (ADR-0058, issue 0004, Tier-2 b1): structural boundaries first (deterministic, no model); any section that would be HARD-SPLIT by the token cap (over-cap -- and structureless by construction, since the structural pass already cut at every heading) is refined by a BOUNDED PER-SECTION model call (`TagBoundaryDiscoverer`, tag-parse), run CONCURRENTLY. The model only ever sees ONE over-cap section at a time, so cost never scales with document length. A section within the cap keeps its structural boundary (no model call); a fully-structured document makes ZERO model calls. `structural`/`fallback` are injectable.

#### `StructuralModelFallbackDiscoverer.discover(self, document) -> 'list[BoundarySpan]'`

_(no docstring)_

#### `StructuralModelFallbackDiscoverer.adiscover(self, document) -> 'list[BoundarySpan]'`

_(no docstring)_

### `IngestionStages(store: 'Any', *, extractor: 'Extractor', segmenter: 'Optional[Segmenter]' = None, span_tagger: 'Optional[SpanTagger]' = None, unit_grouper: 'Optional[UnitGrouper]' = None, boundary_decider: 'Optional[BoundaryDecider]' = None, writer: 'Optional[RecordWriter]' = None, tuning: 'Optional[IngestionTuning]' = None, embedder: 'Any' = None, chunk_model: 'Optional[str]' = None, cache_dir: 'Union[str, Path]', discoverer: 'Any' = None, unit_representative: 'Optional[UnitRepresentative]' = None) -> 'None'`

ING-4c: the engine's per-document ingestion STAGES -- one implementation, driven by `IngestionPipeline.aingest` and by any other driver (the reference contract pipeline runs them as its LangGraph nodes). Each stage enforces its hook contract; a stage failure raises (the driver decides: dead-letter, retry).

#### `IngestionStages.parsed(self, sd: 'Any') -> 'Any'`

The document's parse: its own (`parse_document`), else a one-item-per-line text parse (text-only input).

#### `IngestionStages.document(self, sd: 'Any') -> 'Any'`

_(no docstring)_

#### `IngestionStages.chunk(self, sd: 'Any') -> 'list'`

Chunks; an EMPTY list for a document with no text (a blank page, a sheet holding only attachments) -- it is still recorded with its children, never dead-lettered for having nothing to chunk.

#### `IngestionStages.segment(self, sd: 'Any', chunks: 'list') -> 'list[TaggedSpan]'`

Spans per chunk (tiling checked), whitespace-only spans dropped (nothing to retrieve or extract), page provenance attached, then tagged.

#### `IngestionStages.index(self, sd: 'Any', tagged: 'list[TaggedSpan]', chunks: 'list') -> 'dict'`

Embed + store each span. Returns `{"span_count", "span_failures"}` (a failed span write is reported).

#### `IngestionStages.extract(self, sd: 'Any', tagged: 'list[TaggedSpan]', *, table_mode: 'str' = 'auto') -> 'ExtractStage'`

Group into units (checked), attach table rows, extract each unit concurrently (provenance checked); a failed unit is recorded and skipped.

#### `IngestionStages.write(self, sd: 'Any', extractions: 'list') -> 'int'`

Persist the records (the domain's writer, else `kg_write`). Returns the number of record nodes.

#### `IngestionStages.write_document_node(self, doc_id: 'str', *, parent_id: 'Optional[str]', filename: 'str', media_type: 'str', sha256: 'str') -> 'None'`

_(no docstring)_

#### `IngestionStages.write_child_links(self, parent_id: 'str', child: 'Any', row_spans: 'dict', rep: 'DocumentReport') -> 'None'`

The child's `EmbeddedIn` edge (its first anchor's position) and an `AttachedTo` edge per record link.

### `UnknownDocumentError(unknown: 'list[str]', present: 'list[str]') -> 'None'`

A `documents` scope named a document id that was never ingested into the store (issue 0031). Mirrors compliance's `UnknownComplianceSourceError` (issue 0007): naming a document that does not exist is a caller error, surfaced explicitly rather than silently returning empty. Carries `.unknown` (the offending ids) and `.present` (the FULL known-document set) as structured attributes; the message names every unknown id but samples `.present`, which can run to thousands of documents in a real store.

### `CapabilityRegistry() -> 'None'`

An in-process registry of built capabilities, keyed by the capability name (FR-S.5).

#### `CapabilityRegistry.register(self, name: 'str', *, contract: 'type[BaseModel]', kind: 'EntryKind', display_name: 'Optional[str]' = None, description: 'Optional[str]' = None, tags: 'Optional[list[str]]' = None, response_bounds: 'Optional[ResponseBounds]' = None) -> 'CapabilityRegistration'`

Register a capability by name with its contract and (explicit) kind, emitting its ARD skeleton. Rejects a malformed name, an unknown kind, or a duplicate registration.

#### `CapabilityRegistry.get(self, name: 'str') -> 'CapabilityRegistration'`

The registration for a name, or raise `KeyError` if the name is unknown.

#### `CapabilityRegistry.names(self) -> 'list[str]'`

_(no docstring)_

### `ResponseBounds(*, maxTokens: int = 25000, supportsPagination: bool = False, supportsFiltering: bool = False) -> None`

Internal-only response bounds a callable entry declares (caps tool responses ~25,000 tokens).

### `KgVertexType(name: 'str', properties: 'tuple[tuple[str, str], ...]', unique_index: 'str | None') -> None`

ADR-0067 P5b: a domain KG vertex-type declaration the store creates -- name, its `(property, SQL type)` pairs, and the property to build a UNIQUE index on (if any).

## Hook protocols

Callables you pass to the engine; any function with this signature conforms.

### `GraphExtractor: (**kwargs)`

The extractor seam. Each extractor in the hybrid stack (FR-C.6) implements this, and the graph-extraction capability (T23) iterates over a list of them. The deferred OpenIE path is a future `Extractor` added to that list, behind this same contract, with no change here.

Note: `@runtime_checkable` makes `isinstance(x, Extractor)` a *presence* check only (it verifies `extract` and `name` exist, not their signatures or return type). Signature and output conformance are enforced downstream by `ExtractionResult` validation, which is what the result-validation tests exercise, not `isinstance`.

### `Store: (**kwargs)`

A swappable store. The ArcadeDB implementation is the default; a stub proves swappability.

## Type aliases

## Constants

### `SPAN_TYPE`

str(object='') -> str str(bytes_or_buffer[, encoding[, errors]]) -> str

Create a new string object from the given object. If encoding or errors is specified, then the object must expose a data buffer that will be decoded using the given encoding and error handler. Otherwise, returns the result of object.__str__() (if defined) or repr(object). encoding defaults to sys.getdefaultencoding(). errors defaults to 'strict'.

### `DEFAULT_GENERAL`

str(object='') -> str str(bytes_or_buffer[, encoding[, errors]]) -> str

Create a new string object from the given object. If encoding or errors is specified, then the object must expose a data buffer that will be decoded using the given encoding and error handler. Otherwise, returns the result of object.__str__() (if defined) or repr(object). encoding defaults to sys.getdefaultencoding(). errors defaults to 'strict'.

### `DEFAULT_RETRY`

Configuration for retrying nodes.

!!! version-added "Added in version 0.2.24"

### `INGEST_PARSE_DEADLINE_S`

Convert a string or number to a floating-point number, if possible.

### `LEADING_ENUM`

Compiled regular expression object.

### `RLM_GRANTED_SUBAGENTS`

Built-in mutable sequence.

If no argument is given, the constructor creates a new empty list. The argument must be an iterable if specified.

## Functions

### `canonical_source_doc_id(raw: 'str') -> 'str'`

The ONE canonical filename/title -> `source_doc_id` slug (FR-S.2; HYG-1).

Every ingestion path MUST derive a `source_doc_id` through this function so the same document gets the same id everywhere. Any run of characters outside the delimiter-safe set ``[A-Za-z0-9._-]`` (notably spaces, ``&``, commas) collapses to a single ``_``; leading/trailing ``_`` are stripped. Existing safe delimiters (``-``, ``.``, ``_`` -- e.g. inside ``EX-10.1`` / ``10-Q``) are preserved. Idempotent on an already-canonical id.

The ``_`` replacement (never ``-``) is the fix for the HYG-1 divergence: two ingestion paths slugged the same title with different characters (``FLEET_MAINTENANCE`` vs ``FLEET-MAINTENANCE``), breaking the cross-graph join. An empty result raises rather than silently colliding every empty title into one id.

### `run_extractors(extractors: 'Iterable[Extractor]', chunk_id: 'ChunkId', text: 'str') -> 'ExtractionResult'`

Run every extractor over one chunk and merge into a single anchored `ExtractionResult`.

This is the seam the capability drives: registering a new extractor means adding it to `extractors`, nothing here changes.

### `to_span_record(op: "'Span'", *, document_id: 'str', chunk_doc_start: 'int', dense_vector: 'list[float]', sparse_vector: 'dict[int, float]', primary_tag: 'str' = '', tags: 'list[str] | None' = None) -> 'SpanRecord'`

CU-B2 (ADR-0029): OperativeSpan -> SpanRecord with DOCUMENT-ABSOLUTE offsets.

Composes `doc_start = chunk_doc_start + op.start`, `doc_end = chunk_doc_start + op.end` (the span's chunk-relative offsets shifted by the parent chunk's offset in the canonical document text, CU-B1). The RAW span text (`op.text = body[start:end]`) is stored -- NOT stripped -- so the citation invariant `canonical_document_text[doc_start:doc_end] == span.text` holds byte-faithfully. The caller may embed over `op.text.strip()`; the stored text stays raw for the highlight.

### `model_for(role: 'ModelRole') -> 'str'`

Resolve a role to a model id. Precedence (all config, MS1-2): the ROLE-SPECIFIC override (`RAG_MODEL_<ROLE>`) > the ALL-ROLES override (`RAG_MODEL_ALL`, to point every role at one model for a quick cross-model test) > the documented default. So a run can swap one role, or every role, purely by env.

### `profile_for(model_id: 'str') -> 'ModelProfile'`

The registered profile for a model id, or a safe default profile for an unregistered one.

### `decision_profile(model_id: 'Optional[str]' = None) -> 'DecisionModelProfile'`

The decision-model profile for `model_id` (or `RAG_DECISION_MODEL`, else the built-in default `jev-1.13`). An unregistered id gets a default profile using that id as its served id (so a raw slug still works).

### `build_model(model_id: 'str', *, temperature: 'float' = 0.0, _client_cls: 'type[ChatOpenAI] | None' = None, **overrides: 'Any') -> 'ChatOpenAI'`

Construct the base client for `model_id`, carrying the profile's base `extra_body` (request-level provider routing, e.g. OpenRouter throughput sort -- a config-driven provider flag, ADR-0027).

Model-level retry/timeout (framework connection resilience) are set here; a caller may override either. `_client_cls` lets a caller substitute a thin ChatOpenAI subclass (e.g. astream_text's cost-capturing client, issue 0021); it defaults to the plain client so every other caller is unchanged.

### `build_structured(model_id: 'str', schema: 'Any', *, include_raw: 'bool' = False, temperature: 'float' = 0.0, max_tokens: 'int | None' = None, label: 'str | None' = None) -> 'Runnable'`

A structured-output runnable for `model_id`, driven by its profile.

The profile supplies the method and the optional structured-only `extra_body`; the `extra_body` is bound to this forced structured call only. This is the sole path to `with_structured_output`. The runnable is wrapped in a SINGLE bounded retry layer (`_with_bounded_retry`); the SDK's own retry loop is disabled here (max_retries=0) so the two do not stack into a ~36 min worst case (engine issue 0003 / ADR-0056).

`temperature` defaults to 0 (deterministic-intent); a caller doing best-of-N self-consistency raises it to sample GENUINELY diverse structured completions (the base client's temperature, not a provider flag). `max_tokens` caps the completion length -- a safety net against a model that runs away to the context limit under a schema constraint (observed on self-hosted Gemma-4 with a mis-set chat template).

### `build_tag_structured(model_id: 'str', schema: 'type[BaseModel]', *, include_raw: 'bool' = False, temperature: 'float' = 0.0, max_tokens: 'int | None' = 2048, retries: 'int' = 1, label: 'str | None' = None, fields: 'set[str] | None' = None) -> '_TagStructuredRunnable'`

Drop-in for `models.seam.build_structured`: returns a runnable whose `.invoke(prompt)` yields a validated `schema` instance -- but via CLIENT-SIDE tag parsing (no server guided decoding), so it works on any model/ provider. `max_tokens` defaults to a generous cap (free-text terminates on its own). `label` (ADR-0058) names the stage/call-site in the deadline warning. `fields` (TAGPARSE-INGEST-1b) restricts emission+parsing to a subset of `schema`'s fields -- for per-group ingestion passes over one big schema. `include_raw` is accepted for signature-compat but not supported (no query-side caller uses it).

### `field_kind(ann: 'Any') -> 'tuple[str, type[BaseModel] | None, str]'`

Classify a field annotation into `(kind, submodel, hint)`: - 'scalar' : a single scalar/enum/Literal/bool/number value (submodel None) - 'list_scalar' : `list[<scalar/enum>]` (submodel None) - 'nested' : a single nested `BaseModel` (submodel = that model) - 'nested_list' : `list[<BaseModel>]` (submodel = the item model) Nested kinds (TAGPARSE-INGEST-1a) let a nested extraction schema (a record with nested sub-records or a list of them) round-trip through tags; the emitter/parser recurse.

### `astream_text(model_id: 'str', prompt: 'Any', *, temperature: 'float' = 0.0, max_tokens: 'int | None' = None, label: 'str | None' = None) -> 'str'`

Free-text generation via streaming (ADR-0057, ASYNC-A3). Streams with `stream_chunk_timeout` for precise idle-drip detection, the total `asyncio.timeout` deadline for the whole call, and the bounded transient retries -- accumulating the streamed chunks into the full text (the same value `build_model(...).invoke(prompt).content` produced). `prompt` is a string or a message list.

### `resolve_connection(model_id: 'str') -> 'Connection'`

ADR-0100: resolve a model STRING to its access (backend + base_url + key + the id the backend expects), from its profile. A profile that PINS a `backend` routes there (so different strings can target OpenRouter vs a self-hosted vLLM/Modal server -- mix at will); an un-pinned profile falls back to the global `RAG_SERVING` default (back-compat). `base_url_env`/`api_key_env` on the profile override the per-backend default env vars, so two distinct vLLM/Modal deployments are just two strings.

### `model_deadline_s() -> 'float'`

PS-8b: the total wall-clock deadline (seconds) for one logical model call, across its bounded retries -- the value every seam call runs under (read at call time, so a test or configuration override is honoured).

### `call_description(model_id: 'str', label: 'str | None') -> 'str'`

The model-call description used in the deadline/retry warnings + the timeout message. ADR-0058 side-fix (issue 0004): include the STAGE/call-site (`label`) when the caller supplies it, so a timeout names WHICH stage was cancelled (e.g. `granite-4.2-8b for semantic_chunking.discover`), not just the model.

### `start_generation(*, model: 'str', input: 'Any' = None, label: 'Optional[str]' = None, role: 'Optional[str]' = None, stage: 'Optional[str]' = None, metadata: 'Optional[dict[str, Any]]' = None) -> 'Any'`

Open a Langfuse generation AT THE CALL START and return the observation (or None when tracing is off). Its `start_time` is the moment this is called, so pairing it with `finish_generation` after the call makes the span's OWN duration the real wall-clock latency. This matters because langfuse v4 has no way to back-date a start: a post-hoc emit (open + immediately end) reports a ~0s duration and the real figure survives only in metadata -- the defect engine issue 0048 hit (Langfuse's latency column read 0 for `astream_text`/`span-relevance`). `input` is captured only at `verbose`; label/role/stage go into metadata. Document/job grouping comes from the ambient `traced_run`.

### `finish_generation(gen: 'Any', *, output: 'Any' = None, usage: 'Optional[dict[str, int]]' = None, cost: 'Optional[float]' = None, latency_ms: 'Optional[float]' = None, completion_start_time: 'Any' = None, metadata: 'Optional[dict[str, Any]]' = None) -> 'None'`

Attach a completed call's results to a generation opened by `start_generation` and END it (end_time = now), so the observation's duration is the true latency. No-op when `gen` is None (tracing off). `output` captured only at `verbose`; `usage` = {"input": n, "output": n}; `cost` is the provider's ACTUAL total USD (OpenRouter pass-through, not an engine price table) -> `cost_details` (None when the backend omits it, e.g. self-hosted vLLM, so Langfuse prices from its own table). `completion_start_time` (time to first token) lets Langfuse split queue+prefill from decode -- the attribution engine issue 0048 asked for. `latency_ms` is also kept in metadata (redundant with the now-correct span duration, but exact).

### `tracing_on() -> 'bool'`

_(no docstring)_

### `usage_capturing() -> 'bool'`

True when at least one usage scope is active — the seam checks this to decide whether to capture usage (e.g. request `stream_usage`) on a call that would otherwise not need it.

### `models_dir() -> 'Path'`

The models root (see the module docstring for the resolution order).

### `business_span(name: 'str', **attributes: 'Any') -> 'Iterator[Any]'`

A domain/business span on the AMBIENT tracer (never a new provider). No-op when OTel is absent.

For domain steps / retrieval stats -- NOT for standard LangChain LLM/tool calls (already captured; a manual span would duplicate them).

### `dead_letter(reason: 'str', **fields: 'Any') -> 'dict'`

A terminal dead-letter record: the item is dropped with a `reason` (not raised) so the batch survives.

Put it on the subgraph state's `dead_letter` key; the caller filters out items that carry one. Extra `fields` capture context (the offending id, the exception text, the node) for diagnosis.

### `raw_llm_span(name: 'str', *, model: 'str', system: 'Optional[str]' = None) -> 'Iterator[Any]'`

Instrument a RAW-SDK model call (the one gap: docling-graph/LiteLLM, sandboxed calls) on the ambient tracer, so it shows up in traces with a model name. Call `record_tokens(span, ...)` after the call for usage. Duration is the span's own. No-op when OTel is absent.

### `agenerate_answer_with_model(query: 'str', evidence: 'list[EvidenceItem]', *, model: 'AnswerModel', guidance: 'Optional[str]' = None) -> 'GeneratedAnswer'`

ASYNC-C1 (ADR-0057): the async twin of `generate_answer` -- the single-call baseline strategy on the async generation seam (`model.agenerate`, a true wall-clock deadline on the model call). Identical guarantees: empty evidence abstains WITHOUT a model call; any citation not in the evidence is dropped; an answer left with no valid citation is coerced to an abstention (no claim without a citation, FR-Q.6).

### `answer_model_for(model_id: 'str | None' = None, *, temperature: 'float' = 0.0, max_tokens: 'int | None' = None) -> 'AnswerModel'`

The generation strategy for a model: ALWAYS the free-text + client-side tag-parse path (ADR-0045). Server-side guided decoding is not portable (runs away on self-hosted Gemma 4, ~60s/call on Cerebras), so generation no longer depends on it for any model -- one LLM-agnostic path, so production and evals stay in step. `SeamAnswerModel` remains for an explicit opt-in (constructed directly), but is never the default.

### `ajudge_spans_with_judge(spans: 'list[tuple[str, list[tuple[str, str]]]]', condition: 'Condition', *, ajudge_fn: 'AJudgeFn', max_concurrency: 'int' = 8, timeout_s: 'float | None' = 90.0, timeout_retries: 'int' = 1) -> 'list[RelevanceVerdict]'`

Judge many retrieved spans against one condition CONCURRENTLY (asyncio.gather + Semaphore, the parallel-LLM rule), order preserved. Each `spans` item is `(span_text, matched)`. Each judgement is bounded by `timeout_s` (a hard wall-clock deadline); on the deadline it is retried up to `timeout_retries` times, then falls back to a conservative `uncertain` verdict -- a stalled provider never hangs the sweep. A NON-timeout judge error propagates (a genuine bug is never masked). Every returned span gets a verdict: the list is 1:1 with `spans`.

### `build_arelevance_judge_fn(model_id: 'str', *, guidance: 'Optional[str]' = None, structured_factory=build_tag_structured) -> 'AJudgeFn'`

The `span_relevance_judgment` SKILL runtime: an async relevance judge through the model seam. Given a span's text + the typed properties detected on it (context) + the structured condition, returns a raw `RelevanceVerdict`. The verdict is tag-parsed client-side (ADR-0045); an answer that never parses (after the seam's bounded re-ask) is the conservative `uncertain`, never an error. `structured_factory` is injected for hermetic tests. `guidance` is the domain's addition to the method (see `relevance_method`).

### `finalize_verdict(raw: 'Optional[RelevanceVerdict]') -> 'RelevanceVerdict'`

The deterministic guarantees the SKILL does not own (the applying-capability step): map the raw verdict to the closed vocab (unreadable/None -> `uncertain`, the conservative recall-safe default) and clamp confidence to [0, 1]. Mirrors `compliance_judgment.assemble_finding`'s conservative mapping.

### `disambiguate(results: 'Sequence[ExtractionResult]', *, coreference_resolvers: 'Sequence[CoreferenceResolver]' = (), entity_rules: 'Optional[EntityRules]' = None) -> 'DisambiguationResult'`

Normalize, reject, and cluster the extracted entity mentions into human-verifiable proposals.

Mentions are collected across the extraction results (their `chunk_id` is provenance), non-entities are rejected (never reach T24), survivors are clustered by (normalized key, type), each cluster carries its provenance and weakest confidence, deferred coreference resolvers (if any) rewrite the clusters, and ambiguous near-duplicates are flagged for human decision (never merged). `entity_rules` are the domain's role words and phrases that are not entities (PS-R5b; declared in its pack).

### `resolve_entities(clusters: 'DisambiguationResult', results: 'Sequence[ExtractionResult]', *, resolver: 'EntityResolver', entity_rules: 'Optional[EntityRules]' = None) -> 'ResolutionResult'`

Link clusters to canonical ids and resolve relationship endpoints as one stream (self-loops dropped).

Each cluster resolves to a canonical id (or None) via the injected `EntityResolver` seam (DD-3) — the resolution STRATEGY is the domain's concern, not this capability's. A relationship ref resolves by matching a cluster key first — so a ref that is the same entity as a standalone mention takes that cluster's id (the two-channel dedup, ADR-0004) — falling back to a direct resolver lookup only for a ref with no cluster. A relationship whose two refs resolve to the same non-None id is dropped (self-loop). These invariants are domain-neutral and stay here; only the surface-form lookup is delegated to the resolver. `entity_rules` (the domain's non-entity role words / phrases, PS-R5b) apply to relationship endpoints as in `disambiguate`.

### `to_graph(resolution: 'ResolutionResult') -> 'tuple[list[GraphNode], list[GraphEdge]]'`

Map a `ResolutionResult` to store nodes + edges. A relationship endpoint that has no standalone entity (a ref-only endpoint) gets a minimal node so every edge connects to a vertex.

### `graph_query(start_entity_id: 'str', *, store: 'Store', relationship_type: 'str', max_hops: 'int' = 1, documents: 'Optional[list[str]]' = None) -> 'GraphAnswer'`

Traverse `relationship_type` (a generic edge-type string -- the CALLER names it; no domain default) from `start_entity_id` up to `max_hops` and return cited evidence.

Every edge on every path is surfaced with its `chunk_id` and confidence tag; no edge is dropped or re-weighted by confidence here (that is the generator's job, FR-Q.6). The evidence is unranked.

`documents` (issue 0031): scope the traversal to a workspace's source documents -- EVERY edge on a path must belong to one of them (so a multi-hop path cannot route through an out-of-scope document). `None` = the whole graph; an unknown id RAISES (`UnknownDocumentError`); `[]` = scope-to-nothing (no evidence).

### `query_embedder(*, post: 'Callable[..., dict]' = post_json) -> 'Any'`

The query-side embedder: the remote A100 `/embed` adapter when `STACK_URL` is set, else the local in-process `BGEM3Embedder` (dev/default). One env (`STACK_URL`) moves the query's embed onto the A100.

### `build_ingest_embedder(profile: 'str' = 'bge-m3') -> 'Any'`

The ingest-side SPAN embedder for `profile` (default BGE-M3; `encode_batch`). Raises on an unknown profile.

### `typed_constraint_match_rank(query_constraints: 'set', candidate_props: 'Sequence[tuple[str, set]]', *, match_count_fn: 'MatchCountFn') -> 'MatchRanking'`

Grade each candidate by how many of the query's (dimension, value) constraints its grounded typed props satisfy -- the match semantics are the INJECTED `match_count_fn` (the contract pack supplies KG-5a canonicalization + subsumption via `constraint_match_count`; this mechanism stays domain-free). Returns descending graded order. Recall-safe: the sort is stable, so a zero-match candidate keeps its input position.

### `cosine(a: 'Sequence[float]', b: 'Sequence[float]') -> 'float'`

Cosine similarity; 0.0 when either vector has zero norm (mirrors eval/kg_primary.cosine).

### `post_json(url: 'str', payload: 'dict', timeout: 'int' = 120) -> 'dict'`

_(no docstring)_

### `stack_url() -> 'str | None'`

The co-located A100 stack base URL from `STACK_URL` (the query-encoder serving switch), or None (local).

### `parsed_source_document(source_doc_id: 'str', name: 'str', data: 'bytes', *, cache_dir: 'Any', metadata: 'Optional[dict]' = None, include_hidden_sheets: 'bool' = True, tuning: 'Optional[Any]' = None) -> 'SourceDocument'`

Build a STRUCTURE-BEARING `SourceDocument` from raw document BYTES (PDF/DOCX/HTML/MD): docling-parse ONCE (content-hash gated + cached), carry the `DoclingDocument` on `.parsed` (so the chunker's structural pass fires on real headings), and set `.text` to the flattened text (for the text-consuming stages). This is how a byte-source corpus adapter -- or the product (RuleWright), which hand-builds its ingest -- feeds a real document to the engine; a plain-text `SourceDocument` (no `.parsed`) still uses the text fallback.

ING-4a: a spreadsheet's HIDDEN sheets are ingested by default; `include_hidden_sheets=False` skips them and lists them in `skipped_hidden_sheets`. The choice is part of the parse cache key.

### `aparsed_source_document(source_doc_id: 'str', name: 'str', data: 'bytes', *, cache_dir: 'Any', metadata: 'Optional[dict]' = None, deadline_s: 'float' = 600.0, include_hidden_sheets: 'bool' = True, tuning: 'Optional[Any]' = None) -> 'SourceDocument'`

The ASYNC, deadline-bounded twin of `parsed_source_document` (ADR-0057) -- STABLE PUBLIC API. Runs the sync build (docling parse + the tiered OCR/VLM escalation, the slowest call in the pipeline) OFF the event loop (`to_thread`) under an `asyncio.timeout`, so a hand-built async ingest can parse a document into the structure-bearing `SourceDocument` the chunker needs WITHOUT reimplementing the wrapper (or blocking the loop). Same caveat as every `to_thread` bound: the deadline unblocks the CALLER; the docling worker thread finishes in the background (true cancellation would route the vision call through the async model seam).

### `parsed_text_document(source_doc_id: 'str', text: 'str', parse_dir: 'Any')`

text -> a `ParsedDocument` (one TextItem per non-blank line), cached -- so the standard `chunk()` path (which loads a real DoclingDocument) works from a text-only source. ING-4c: moved here from the contract pipeline (generic mechanism; the contract pipeline re-exports it as `_parsed_from_text`).

### `parse_docling_bytes(name: 'str', data: 'bytes', *, parser: 'Any' = None) -> 'Any'`

Parse raw document BYTES into a `DoclingDocument`. `.txt`/`.md` bytes are wrapped directly; binary docs (PDF/DOCX/HTML) go through docling. `parser` defaults to the tiered OCR parser (0009-WIRE2), injectable for tests. `name` supplies the file extension docling needs to pick a backend.

### `aparse_docling_bytes(name: 'str', data: 'bytes', *, parser: 'Any' = None, deadline_s: 'float' = 600.0) -> 'Any'`

ASYNC-bounded document parse (ADR-0057): run the sync `parse_document_bytes` (incl. the tiered VLM escalation -- the slowest call in the pipeline) OFF the event loop via `to_thread`, under a wall-clock `asyncio.timeout` so a hung/slow OCR never stalls the async ingestion. NOTE: `to_thread` cannot cancel the worker thread, so the deadline unblocks the CALLER (raises TimeoutError); the docling parse thread finishes in the background. True cancellation would require routing the vision call through the async model seam.

### `document_to_sections(doc: 'Any') -> 'list[dict]'`

A parsed document -> `[{section, heading, text, pages, bbox}]` split at its headings -- the compliance side's shape (`RegulationAdapter` ingests exactly this). Body before the first heading is kept as a leading section (heading ""), so nothing is dropped. PAGE_HEADER and whitespace-only items are skipped.

issue 0043: each section carries `pages` (the source page(s) its items span, from the parse's per-item provenance -- present even on a scan) and a best-effort `bbox`: a single-item section reports that item's box, a multi-item section reports None (a section is not one rectangle, and a fabricated box is worse than none).

### `is_bare_heading(text: 'str') -> 'bool'`

A bare SECTION HEADING (e.g. '9. Scope of Work') -- a short enumerated/Title-case line with NO sentence terminator. It must fold INTO its body, never stand alone: a standalone heading gets labelled as a unit that points at a bare heading, which pollutes evidence and can hide the real passage (issue 0006). A genuine short section carries an operative sentence (terminal '.'/';'/':'), so it is NOT a heading.

### `section_number(heading: 'str', index: 'int') -> 'str'`

A short section id: the leading numeric token of the heading (e.g. '1' from '1. Introduction'), else the 1-based position -- so a section citation is stable and human-meaningful.

### `achunk_texts(document: 'Any', *, discoverer: 'Optional[BoundaryDiscoverer]' = None, token_cap: 'int' = 20000, model_id: 'str | None' = None) -> 'list[str]'`

ASYNC twin of `chunk_texts` (SEG-2/SEG-5): awaits the discoverer's async boundary call, so it runs on the subject verdict's event loop. Same reuse, same default = the production ingestion `StructuralModelFallbackDiscoverer`.

### `validate_documents(store: 'Any', documents: 'Optional[list[str]]') -> 'None'`

Validate a `documents` scope against `store.known_document_ids()` BEFORE any retrieval spends (mirrors issue 0007's `_validate_sources`). `None` (whole store) is not validated. `[]` (scope-to-nothing) is a valid empty scope, not an error -- the retrieval surfaces short-circuit it. A non-empty list with any id absent from the store raises `UnknownDocumentError`. A store without `known_document_ids` (a minimal fake) is treated as un-validatable and passes through.

### `capability_impl(name: 'str') -> 'Callable[..., Any]'`

Resolve a capability name to its invoke factory via the manifest `impl_ref`. Raises `KeyError` if the name is unknown to the ARD catalog, `NotImplementedError` if it declares no `impl_ref` (not invokable by name).

### `load_kg_schema(path: 'str') -> 'tuple[tuple[KgVertexType, ...], frozenset[str]]'`

ADR-0067 P5b: the DOMAIN KG node/edge storage schema from the ttl -- `(vertex types, structural edge names)`. The engine infra (Chunk/Span/Entity) stays generic in store code; these domain types are pack-declared. Cached. `path` is the pack's own `.ttl` (AC-journey); there is no default pack (ING-8a/8b).

### `map_concurrent(items: 'Iterable[T]', fn: 'Callable[[T], R]', *, max_concurrency: 'int' = 8, progress_path: 'Optional[Path]' = None, label: 'str' = '', every: 'int' = 1, echo: 'bool' = False, timeout_s: 'Optional[float]' = None, timeout_retries: 'int' = 1) -> 'list[R]'`

Synchronous convenience for callers not already in an event loop: bounded-concurrent map with a flushed progress file and/or a stdout echo (`done/total`, rate, ETA), so progress is visible live. `timeout_s` bounds each call with a hard wall-clock deadline (a stalled call -> `None`, never a hang).
