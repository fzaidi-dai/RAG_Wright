# API reference — `rag_wright.api`

> **Generated** from the live `rag_wright.api.__all__` by `scripts/build_api_docs.py` — do not edit by hand. Regenerate with `bash scripts/build_api_docs.sh`; the test suite fails if it drifts (`tests/arch/test_api_docs_current.py`). The whole public surface is imported from `rag_wright.api`.

## Types

### `EngineConfig(store: 'StoreConfig', models: 'dict[str, str]' = <factory>, embeddings: 'dict[str, str]' = <factory>, options: 'EngineOptions' = <factory>, pack: 'Optional[str]' = None) -> None`

The product's view of the engine: the store connection, chosen models (`models`: `ModelRole` value -> model alias), the embedding profile, the `options` catalog (generic `ingest` knobs + each pack's options under `packs`), and `pack`: the path to the domain pack `.ttl` whose KG types `open_workspace` creates (None = only the neutral engine types: Chunk, Entity, Relationship, Mentions, Span, Document, EmbeddedIn, AttachedTo). Defaults just work; override only to trade quality/cost/latency. Implementation details (ArcadeDB, BGE) never cross this boundary.

### `StoreConfig(host: 'str', port: 'str', user: 'str', password: 'str', backend: 'str' = 'arcadedb', protocol: 'str' = 'http') -> None`

How to reach the KG/retrieval store. `backend` selects the implementation (only `arcadedb` today; a new backend -- e.g. Neo4j -- is added here, invisibly to the product).

### `EngineOptions(ingest: 'IngestOptions' = <factory>, packs: 'Mapping[str, Any]' = <factory>) -> None`

The engine's options catalog: the generic ingest knobs, plus `packs` -- each domain pack's own options object keyed by the pack's name (ING-8d), e.g. `packs={"contracts": ContractIngestOptions(...)}`. The engine passes `packs` through untouched; a pack reads its entry and falls back to its defaults when absent.

### `IngestOptions(tuning: 'Optional[IngestionTuning]' = None) -> None`

The engine's generic ingest knobs, settable through config instead of environment variables (EP-API-4a). ING-8d: only engine mechanism lives here; a domain pack's own knobs travel in `EngineOptions.packs`.

### `WorkspaceHandle(store: 'Any', config: 'EngineConfig', corpus: 'str') -> 'None'`

An opaque handle to a resolved engine workspace. Public surface: `model_id(role)` and the read-only `config` (a pack reads its options from `config.options.packs`) and `embedder` (PS-39). The resolved store is engine-internal (`_store`); pack code reaches it with `pack_store(ws, cls)`.

#### `WorkspaceHandle.model_id(self, role: 'ModelRole') -> 'str'`

Resolve a model role to its id: the `EngineConfig.models` override wins, else the profile default.

### `ModelServerSpec(max_num_seqs: 'int', name: 'str' = 'rw-qwen3-modal', model: 'str' = 'Qwen/Qwen3.8-27B-FP8', kv_cache_dtype: 'str' = 'fp8', served_name: 'str' = 'Qwen/Qwen3.8-27B', gpu: 'str' = 'A100-80GB:1', tensor_parallel: 'int' = 1, max_len: 'int' = 16384, gpu_util: 'float' = 0.95, tool_parser: 'str' = 'hermes', reasoning_parser: 'str' = 'qwen3', image_builder_version: 'str' = '2025.06', api_key_env: 'str' = 'VLLM_API_KEY') -> None`

One self-hosted Qwen server on Modal. The defaults are the locked setup of the `qwen-vllm-modal` skill (ADR-0110, Config B): FP8 weights + FP8 KV cache, one A100-80GB, tensor parallel 1, 16K context, 0.95 GPU memory, served as `Qwen/Qwen3.8-27B` with the hermes tool parser and the qwen3 reasoning parser, image builder 2025.06. `max_num_seqs` has no default: ADR-0110 sets it to the real concurrency target (keep client concurrency below it). `name` is the Modal app name, and so the server's URL. `api_key_env` names the environment variable that holds the server's API key.

#### `ModelServerSpec.deploy_env(self) -> 'dict[str, str]'`

The deploy command's settings, as the skill's documented command passes them.

### `ModelServer(name: 'str', root: 'str', base_url: 'str', model_id: 'str', served_model_id: 'str', api_key_env: 'str') -> None`

A deployed server: `model_id` is the model id to use (in `EngineConfig.models` or a `model=` argument); `base_url` is its OpenAI-compatible endpoint; `root` is its URL (for `/health`).

### `ModelServerNotDeployed(name: 'str')`

No deployed model server has this name (never deployed, or stopped).

### `ModelServerStatus(name: 'str', deployed: 'bool', running: 'bool', healthy: 'bool', weights_cached: 'bool') -> None`

What `amodel_server_status` found. `deployed`: the Modal app exists. `running`: a container is up (the GPU is billing); False when scaled to zero after idling. `healthy`: a running container answers `/health` (False while it starts). `weights_cached`: the spec's model weights are in the weights Volume, so a deploy or cold start skips the ~31 GB download (an empty `modal app list` says nothing about this).

### `DecisionModelProfile(*, model_id: str, served_model_id: Optional[str] = None, endpoint: str = 'https://openrouter.ai/api/alpha/decisions', api_key_env: str = 'OPENROUTER_API_KEY', timeout_s: float = 60.0, op_threshold: float = 0.5, multilabel_threshold: float = 0.6) -> None`

How to reach a typed-DECISION model (TypeSafe Jev, or an on-prem Laya decisions server). This is a DIFFERENT API surface from `ModelProfile`: a decision model POSTs a `state` + typed `questions` to a Decisions endpoint and returns calibrated typed answers (`noul`/`choice`/`score`), not chat completions -- so it needs its own profile shape (endpoint + served id + key env + default thresholds), not the structured-output fields.

Keyed by model id so swapping `jev-1.13` -> `jev-latest`, or Jev -> an on-prem Laya decisions endpoint, is a config change, not a code edit (the Laya fallback, ADR-0119). The capability (`capabilities/jev_decision.py`) reads this; the model id is never hardcoded in capability/node code (the model-neutrality rule).

### `ModelProfile(*, model_id: str, structured_method: Literal['function_calling', 'json_mode', 'json_schema'] = 'function_calling', client_side_structured: bool = False, structured_extra_body: Optional[dict[str, Any]] = None, text_extra_body: Optional[dict[str, Any]] = None, agent_extra_body: Optional[dict[str, Any]] = None, structured_max_tokens: Optional[int] = None, extra_body: Optional[dict[str, Any]] = None, backend: Optional[Literal['openrouter', 'vllm', 'ollama']] = None, served_model_id: Optional[str] = None, base_url: Optional[str] = None, base_url_env: Optional[str] = None, api_key_env: Optional[str] = None, max_concurrency: Optional[int] = None) -> None`

How a given model id must be driven for a forced structured-output call.

### `ModelRole(*values)`

Which model does which job. The mapping to ids lives in config, not in capability code.

Members: `STRUCTURED_REASONING` (`'structured_reasoning'`), `STRUCTURED_REASONING_SECONDARY` (`'structured_reasoning_secondary'`), `GENERAL` (`'general'`), `SUMMARIZATION` (`'summarization'`), `FUNCTION_CLASSIFY` (`'function_classify'`), `VISION_OCR` (`'vision_ocr'`)

### `Discovered(slug: 'str', kind: 'str', description: 'str', representative_queries: 'tuple[str, ...]', score: 'float') -> None`

One ranked capability match from `discover` — enough for an agent to pick and invoke it by `slug`.

### `SpanHit(*, span_id: str, text: str, document_id: str, doc_start: Optional[int] = None, doc_end: Optional[int] = None, pages: list[int] = [], bbox: Optional[tuple[float, float, float, float]] = None, primary_tag: str = '', rank: int, found_by: tuple[str, ...]) -> None`

One cited span from `span_search`, best first. `found_by` says which legs found it: `"hybrid"` (the fused dense + sparse search) and/or `"dense"` (the dense floor). `bbox` is `(l, t, r, b)` or None.

### `KgNode(type: 'str', key_field: 'str', props: 'dict[str, object]') -> None`

A typed KG node to upsert (DD-1b, ADR-0117): `type` is the vertex type, `key_field` the identity field to upsert on, `props` the fields (including `key_field`) as DOMAIN-NATIVE values. The store encodes each prop by its pack-declared storage type -- the caller never serializes to the store's wire format.

### `KgEdge(type: 'str', from_type: 'str', from_key_field: 'str', from_key: 'object', to_type: 'str', to_key_field: 'str', to_key: 'object', props: 'dict[str, object]') -> None`

A typed KG edge to create between two nodes identified by (type, key_field, key). `props` are native values (edge properties are type-driven: edges declare no storage schema).

### `Range(gt: 'Optional[object]' = None, ge: 'Optional[object]' = None, lt: 'Optional[object]' = None, le: 'Optional[object]' = None) -> None`

A `where` value meaning a comparison, evaluated in the store (ER-1): `gt` (>), `ge` (>=), `lt` (<), `le` (<=), at most one lower and one upper bound, at least one bound. Bounds are scalars (numbers compare as numbers, strings lexically). Any number of fields may carry one, beside equality, membership and `NOT_NULL`, on every KG call that takes `where`: `where={"weight": Range(ge=180, le=220), "tensile": Range(gt=400)}`.

#### `Range.terms(self) -> 'list[tuple[str, object]]'`

`[(operator, bound), ...]`, lower bound first.

### `BlankPageText(*, source: str, pages: dict[int, str], cache_files: list[str]) -> None`

One cached parse with text on blank pages. `source` is the PDF's path (or the name given with its bytes), `pages` maps each blank page to the text the cached parse holds there, and `cache_files` are the cached parse(s) and their OCR sidecar: delete them, then re-ingest the document so it is parsed again.

### `EvidenceItem(*, chunk_id: str, text: str, confidence: Optional[str] = None, properties: Optional[list[dict]] = None) -> None`

One piece of grounding evidence: a chunk's text, its `chunk_id` (the citation), and — for a graph-derived fact — its confidence tag (surfaced to the generator, FR-S.4).

Engine issue 0011 / ADR-0064: an evidence item's typed properties ride OUT-OF-BAND here, NOT concatenated into `text`. `_evidence_block` renders only `text`, so the generator never sees the `dimension=value` schema tokens and cannot paraphrase them into prose ("the typed property <dimension>=..."). The structured facts stay available on this field for a caller that wants them (the product's UI chips); they are never fed to the model. This is the ADR-0054 treatment (a function label) applied to properties, but out-of-band rather than dropped, because the properties do real work elsewhere.

### `GeneratedAnswer(*, answer: str, citations: list[str], abstained: bool = False, answer_kind: Optional[rag_wright.capabilities.answer_generator.AnswerKind] = None) -> None`

The generated answer (FR-C.9): grounded text, the cited chunk_ids, whether it abstained, and its sufficiency `answer_kind` (PREC-1a). `abstained` is kept (backward-compat) and `answer_kind` is kept in sync: constructing with `abstained` alone derives the kind (ABSTAINED/ANSWERED); passing `answer_kind` (e.g. PARTIAL) wins and sets `abstained` accordingly. So no existing `abstained=`-only caller changes.

### `AnswerKind(*values)`

The sufficiency of a generated answer (PREC-1a): a first-class signal so an honest hedge is distinct from a confident over-answer, both in the contract the caller receives and in evaluation.

Members: `ANSWERED` (`'answered'`), `PARTIAL` (`'partial'`), `ABSTAINED` (`'abstained'`)

### `RelevanceVerdict(*, verdict: str, rationale: str = '', confidence: float = 0.0) -> None`

The raw structured output of one relevance judgement (the `span_relevance_judgment` SKILL's typed output). `verdict` is a loose string mapped to the closed `Relevance` vocab by the applying capability (unreadable -> uncertain, the conservative default).

### `Relevance(*values)`

The closed relevance vocab. `uncertain` is both a real judgement (ambiguous text) AND the conservative default when the judge could not be read (recall-safe: never a fabricated not_found).

Members: `RELEVANT` (`'relevant'`), `NOT_RELEVANT` (`'not_relevant'`), `UNCERTAIN` (`'uncertain'`)

### `Condition(*, category: str, value_condition: Optional[str] = None, question: Optional[str] = None) -> None`

The structured test a retrieved span is judged against (issue 0023). `category` is primary (the kind of passage searched for, in the domain's own terms); `value_condition` is a narrower test within it (often absent or shared across a multi-condition search); `question` is CONTEXT ONLY -- in a multi-condition search it belongs to all conditions at once, so it must not by itself make a span relevant.

### `UsageTotals(calls: 'int' = 0, input_tokens: 'int' = 0, output_tokens: 'int' = 0, cost_usd: 'float' = 0.0, calls_without_cost: 'int' = 0, latency_ms_total: 'float' = 0.0, by_model: 'dict[str, ModelUsage]' = <factory>, _lock: 'threading.Lock' = <factory>) -> None`

The usage accumulated within one `measure_usage()` block: top-level totals (calls, tokens, `cost_usd` for the calls whose cost is known, `calls_without_cost`, latency) + a per-model breakdown in `by_model`. Decision-model (Jev) calls and vision-OCR pages are counted too (OCR pages as calls without a cost).

### `ModelUsage(calls: 'int' = 0, input_tokens: 'int' = 0, output_tokens: 'int' = 0, cost_usd: 'float' = 0.0, calls_without_cost: 'int' = 0, latency_ms_total: 'float' = 0.0) -> None`

Per-model totals within a scope (`cost_usd` sums KNOWN per-call costs only).

### `CapabilityManifest(slug: 'str', kind: 'EntryKind', display_name: 'str', description: 'str', representative_queries: 'tuple[str, ...]', tags: 'tuple[str, ...]' = (), requires: 'tuple[str, ...]' = (), skill_runtime: 'Optional[SkillRuntime]' = None, golden_eval_ref: 'Optional[str]' = None, response_bounds: 'Optional[ResponseBounds]' = None, capability_interface: 'Optional[CapabilityInterface]' = None, impl_ref: 'Optional[str]' = None) -> None`

The committed ARD authoring data for one capability (what registration cannot derive).

### `LayoutItem(*, kind: Literal['title', 'heading', 'paragraph', 'list_item', 'table', 'caption', 'footnote', 'page_header', 'page_footer', 'code', 'formula', 'form', 'other'], text: str, start: int, end: int, level: Optional[int] = None, pages: list[int] = []) -> None`

One layout element of the parsed document that overlaps a chunk, in CHUNK-relative offsets.

### `Span(*, span_id: str, parent_chunk_id: str, span_index: int, start: int, end: int, text: str, pages: list[int] = [], bbox: tuple[float, float, float, float] | None = None, kind: Optional[Literal['title', 'heading', 'paragraph', 'list_item', 'table', 'table_row', 'caption', 'footnote', 'page_header', 'page_footer', 'code', 'formula', 'form', 'other']] = None) -> None`

One span: the smallest citeable unit, indexed for retrieval. It points back to its parent chunk; `span_id` is `<parent_chunk_id>#<span_index>` (identifier rule) and `start`/`end` are offsets into the chunk text.

### `TaggedSpan(*, span: rag_wright.contracts.ingestion.Span, tags: list[str] = [], scores: dict[str, float] = {}, primary: Optional[str] = None) -> None`

A span with the optional span tagger's soft tags (primary first) and their scores. `primary` states the primary tag explicitly when it is not simply the first tag (e.g. a placeholder for an untagged span).

### `Unit(*, index: int, anchor: rag_wright.contracts.ingestion.Span, spans: list[rag_wright.contracts.ingestion.Span], text: str, tags: list[str] = [], table_row: Optional[rag_wright.contracts.ingestion.TableRow] = None) -> None`

The extraction unit: consecutive spans grouped by the unit grouper. `text` is what the extractor reads; `anchor` is the citation anchor for records read from it (a member span).

### `UnitExtraction(*, nodes: list[rag_wright.store.seam.KgNode] = [], edges: list[rag_wright.store.seam.KgEdge] = []) -> None`

What an extractor returns for one unit: typed KG nodes/edges in the pack's schema (types the pack `.ttl` declares). Provenance (FR-S.4, enforced by `check_extraction`): a fact node or edge carries `span_id` (a span of this unit) and `confidence` (a `ConfidenceTag` value) as props; nodes without a `span_id` are shared vocabulary (value or taxonomy nodes); a non-empty extraction must cite at least once.

### `IngestionContractError`

A hook's output broke the ingestion contract (tiling, unit integrity, or record provenance).

### `IngestionTuning(*, max_unit_chars: int = 6000, min_fragment_alnum: int = 2, record_table: rag_wright.contracts.ingestion.RecordTableRule = RecordTableRule(min_header_cols=3, distinct_ratio=0.8, serial_ratio=0.8, min_cols=8), identifier: rag_wright.contracts.ingestion.IdentifierRule = IdentifierRule(max_rows=3, max_files=3), extract_concurrency: int = 8, document_concurrency: int = 2) -> None`

Every structural threshold of the default ingestion hooks, in one place (defaults = the evaluated values). Mechanism tuning, not domain knowledge (ADR-0066): set it from `evaluate_ingestion` runs on your own samples.

### `RecordTableRule(*, min_header_cols: int = 3, distinct_ratio: float = 0.8, serial_ratio: float = 0.8, min_cols: int = 8) -> None`

When `auto` treats a table as a DATABASE (one unit per row): a header of `min_header_cols`+ named columns, `distinct_ratio`+ of them distinct (a merged cell's adjacent repeats count once), and either a serial first column in `serial_ratio`+ of the rows or `min_cols`+ columns.

### `IdentifierRule(*, max_rows: int = 3, max_files: int = 3) -> None`

What counts as a record IDENTIFIER when linking an embedded file to a row: a token (4+ chars, contains a digit) on at most `max_rows` table rows and in at most `max_files` embedded files.

### `IngestSource(*, path: Optional[str] = None, data: Optional[bytes] = None, name: Optional[str] = None, doc_id: Optional[str] = None, table_mode: Literal['auto', 'record', 'block'] = 'auto', include_hidden_sheets: bool = True, metadata: Optional[dict[str, Any]] = None) -> None`

One document to ingest: either a file `path`, or in-memory `data` (bytes, e.g. an upload) with its file `name` (the extension picks the format); its `doc_id` (default: derived from the file name), how its tables are grouped (`auto` decides per table; `record` / `block` force one mode), whether hidden spreadsheet sheets are ingested, and the product's own `metadata` (PS-13): written onto the document's `Document` node in the same write as the engine's fields, and onto each embedded file's. Keys are identifiers that do not shadow an engine field (`doc_id`, `parent_doc_id`, `filename`, `media_type`, `sha256`); values are strings, numbers, booleans, None or lists of those. Read or filter it with `kg_read(ws, "Document", where={...})`; change it later with `kg_update`.

#### `IngestSource.read_bytes(self) -> 'bytes'`

The document's bytes: `data`, or the file at `path`.

### `IngestionPipeline(extractor: 'Extractor', *, segmenter: 'Optional[Segmenter]', span_tagger: 'Optional[SpanTagger]', unit_grouper: 'Optional[UnitGrouper]', boundary_decider: 'Optional[BoundaryDecider]', writer: 'Optional[RecordWriter]', tuning: 'Optional[IngestionTuning]', embedder: 'Any', chunk_model: 'Optional[str]', progress: 'Callable[[str], Any]', document_hook: 'Optional[DocumentHook]' = None, unit_representative: 'Optional[UnitRepresentative]' = None, chunk_discoverer: 'Any' = None) -> 'None'`

Built by `build_ingestion`; run with `await pipeline.aingest(ws, sources, cache_dir=...)`. Drives the shared `IngestionStages` per document (parse -> chunk -> segment -> tag -> index -> group -> extract -> write -> `document_hook` -> `Document` node), then ingests embedded files and PDF attachments the same way, as child documents linked to their parent.

#### `IngestionPipeline.stages(self, ws: 'Any', *, cache_dir: 'Union[str, Path]') -> 'IngestionStages'`

Advanced: the individual stage functions bound to a workspace (its store, ingest embedder and tuning), for a domain that drives the stages itself (the reference contract pipeline does). Most domains only need `aingest`.

#### `IngestionPipeline.aingest(self, ws: 'Any', sources: 'Sequence[Union[str, Path, IngestSource]]', *, cache_dir: 'Union[str, Path]') -> 'IngestionReport'`

Ingest every source (and its embedded children) into the workspace `ws` (from `open_workspace`). `sources` are file paths or `IngestSource`s (a path, or in-memory bytes with a file name); `cache_dir` holds the content-hash-gated parse and chunk caches (a re-ingest of an unchanged file re-uses them). Documents run concurrently up to `tuning.document_concurrency`; a document that fails is dead-lettered in its `DocumentReport`, never raised. Returns the `IngestionReport`. Every model role resolves through the workspace's `EngineConfig.models` (PS-14).

### `IngestionReport(documents: 'list[DocumentReport]' = <factory>) -> None`

The result of `IngestionPipeline.aingest`: one `DocumentReport` per document (embedded children included). `failed` counts the dead-lettered documents and `succeeded` the rest (properties).

### `DocumentReport(doc_id: 'str', parent_doc_id: 'Optional[str]' = None, chunks: 'int' = 0, spans: 'int' = 0, units: 'int' = 0, records: 'int' = 0, extraction_failures: 'list[dict]' = <factory>, span_failures: 'list[dict]' = <factory>, skipped_hidden_sheets: 'list[str]' = <factory>, embedded_skipped: 'list[str]' = <factory>, children: 'list[str]' = <factory>, links: 'dict[str, int]' = <factory>, unmapped_links: 'int' = 0, dead_letter: 'Optional[str]' = None) -> None`

What ingesting one document did. Counts: `chunks`, `spans` (indexed), `units` (extracted), `records` (nodes written). `extraction_failures` (`{unit, anchor, reason}`) / `span_failures` (`{span_id, reason}`): the units / spans whose extraction or indexing failed; the rest of the document still lands. `skipped_hidden_sheets`: hidden spreadsheet sheets left out (`IngestSource.include_hidden_sheets=False`). `children`: the ids of embedded files and PDF attachments ingested as child documents (`parent_doc_id` is set on theirs); `embedded_skipped`: embedded files that could not be ingested. `links`: `AttachedTo` record links written, by confidence; `unmapped_links`: links whose record row could not be found. `dead_letter`: why the whole document failed (None when it landed).

### `IngestionEvaluation(documents: 'list[DocumentEvaluation]' = <factory>, failures: 'list[str]' = <factory>, unlabelled_tables: 'list[str]' = <factory>, labels: 'Optional[LabelEvaluation]' = None, warnings: 'list[str]' = <factory>) -> None`

The result of `evaluate_ingestion`: per-document measures, the `failures` (each check that did not hold, by document), `unlabelled_tables` (tables no `table_labels` pattern matched) and `warnings` (what a person should check; never a failure). `passed` (property) is True when there are no failures.

#### `IngestionEvaluation.to_dict(self) -> 'dict[str, Any]'`

The result as JSON-ready data, `passed` included (EV-1: a backend returns it as is).

### `DocumentEvaluation(doc: 'str', tiles: 'bool' = True, spans: 'int' = 0, units: 'int' = 0, table_rows: 'int' = 0, table_row_integrity: 'Optional[float]' = None, layout_respect: 'Optional[float]' = None, bare_heading_spans: 'int' = 0, headings_start_units: 'Optional[float]' = None, tables: 'int' = 0, tables_whole: 'Optional[float]' = None, furniture_in_units: 'int' = 0, coverage: 'Optional[float]' = None, cap_ok: 'bool' = True, table_modes: 'list[dict]' = <factory>, embedded_children: 'int' = 0, label_results: 'list[dict]' = <factory>, unsure_continuations: 'list[str]' = <factory>, error: 'Optional[str]' = None) -> None`

One sample document's structural measures. Segmentation: `tiles` (spans tile every chunk), `table_row_integrity` (share of table rows that are their own span), `layout_respect` (share of spans inside one layout item), `bare_heading_spans`. Grouping: `headings_start_units` (share of headings that start a unit), `tables_whole` (share of tables kept whole or split by the record/header rule), `furniture_in_units` (page furniture that leaked into units), `coverage` (share of content spans in some unit), `cap_ok` (every unit within `max_unit_chars`). `table_modes`: the mode chosen per table. `unsure_continuations`: sheet tables that may continue past a blank row (PS-26). `error`: why the document could not be evaluated.

### `LabelEvaluation(gold: 'int' = 0, correct: 'int' = 0, accuracy: 'Optional[float]' = None, per_label: 'dict[str, dict]' = <factory>, confusions: 'list[dict]' = <factory>, unmatched: 'list[str]' = <factory>, ambiguous: 'list[str]' = <factory>, conflicting: 'list[str]' = <factory>) -> None`

Unit labelling against the gold (`evaluate_ingestion(unit_labels=...)`), scored per UNIT: snippets that land in the same unit with the same label count once. `gold`: units scored; `correct`: those whose label matches; `accuracy`: correct / gold. `per_label`: `{label: {gold, predicted, correct, recall, precision}}` over the gold units (precision is among the gold units predicted with that label; `None` when none were). `confusions`: `{gold, predicted, count}` for every wrong pair, most frequent first. `unmatched` / `ambiguous`: `"<doc>: <snippet>"` for a snippet found in no unit / in several. `conflicting`: `"<doc>: unit <i>: <labels>"` for a unit whose snippets carry different labels (not scored: one label cannot match them all). An empty label means the unit should carry none.

### `GoldItem(*, id: Annotated[str, MinLen(min_length=1)], input: Any, expected: Any, group: Optional[str] = None, provenance: Literal['hand', 'silver', 'generated'] = 'hand', metadata: dict[str, typing.Any] = <factory>) -> None`

One gold case. `id` is stable across versions (a backend keys runs on it).

### `GoldSet(*, name: Annotated[str, MinLen(min_length=1)], version: Annotated[str, MinLen(min_length=1)], items: Annotated[list[rag_wright.contracts.evals.GoldItem], MinLen(min_length=1)], local_only: bool = True, description: str = '') -> None`

A named, versioned set of gold items. `local_only` (default True) keeps it off any hosted backend.

#### `GoldSet.groups(self) -> 'dict[Optional[str], list[str]]'`

Item ids by grouping key, in item order (split on these, never on items).

### `GoldMapping(*, input: Union[str, list[str]], expected: Union[str, list[str]], id: Optional[str] = None, group: Optional[str] = None, provenance: Optional[str] = None, metadata: list[str] = <factory>) -> None`

Which columns (or JSONL keys) hold what. `input` / `expected`: one column gives its value, several give a `{column: value}` record. `id`: the column with a stable item id (None = where the row is). `group`: the leakage-safe grouping key's column. `provenance`: a column of `hand` / `silver` / `generated` (blank = the default). `metadata`: columns copied onto each item.

#### `GoldMapping.columns(self) -> 'list[str]'`

_(no docstring)_

### `GoldImportError`

The file cannot be read into a correct gold set; the message says where and why.

### `EvalReport(*, gold_set: str, gold_version: str, task: str, engine_version: str, models: list[str] = <factory>, repeats: Annotated[int, Ge(ge=1)] = 1, items: list[rag_wright.contracts.evals.ItemResult], scores: list[rag_wright.contracts.evals.ScorerResult], created_at: datetime.datetime = <factory>, run_url: Optional[str] = None, run_urls: list[str] = <factory>, dataset_version: Optional[str] = None) -> None`

The JSON-ready result of an eval run: what was measured, per item, per scorer.

### `ItemResult(*, item_id: str, outputs: list[typing.Any], errors: list[typing.Optional[str]]) -> None`

One gold item's outputs and errors, one slot per repeat (`None` error = the call answered).

### `ScorerResult(*, scorer: str, passed: bool, gate: dict[str, typing.Optional[float]] = <factory>, thresholds: dict[str, float] = <factory>, diagnostics: dict[str, typing.Any] = <factory>) -> None`

One scorer's verdict: `gate` holds the gated measures, `thresholds` what they must reach, `diagnostics` what is reported and never gated.

### `TableRow(*, table_ref: str, sheet: Optional[str] = None, page: Optional[int] = None, row_index: int, columns: list[str], values: list[str]) -> None`

ING-7: one data row of a parsed table, read from the parse's cell GRID (exact cell text, whitespace collapsed) -- whole even when the chunker split the table. `columns` is the table's first row as parsed; `row_index` is the 0-based grid row (data rows start at 1). Domain-neutral: what a column MEANS is the domain's decision.

#### `TableRow.cell(self, column: 'str') -> 'Optional[str]'`

The value under the first column named `column` (exact match), or None.

## Hook protocols

Callables you pass to the engine; any function with this signature conforms.

### `Segmenter: (chunk_id: 'str', text: 'str', layout: 'Sequence[LayoutItem]') -> 'list[Span]'`

chunk -> spans that tile its text. Sync (CPU). `layout` is the parse's layout overlapping the chunk (empty for a text-only source).

### `SpanTagger: (chunk_text: 'str', spans: 'Sequence[Span]') -> 'Awaitable[list[TaggedSpan]]'`

Optional: soft tags per span of one chunk, aligned to `spans`.

### `UnitGrouper: (spans: 'Sequence[TaggedSpan]', *, decider: 'Optional[BoundaryDecider]' = None) -> 'Awaitable[list[Unit]]'`

A document's spans (in order, across chunks) -> extraction units. May drop spans (e.g. page furniture); a dropped span stays in the span index. `decider` (a `BoundaryDecider`: candidate line texts -> "starts a new unit?" per text), when set, settles the boundaries the grouper's rules are unsure of.

### `UnitRepresentative: (members: 'Sequence[TaggedSpan]') -> 'TaggedSpan'`

PS-R3: which member span represents a unit -- a DOMAIN decision. The engine applies it after grouping (to any grouper's units): the chosen span becomes the unit's `anchor` (the citation its records carry and the span their provenance points to) and its primary tag leads the unit's `tags` (the label the extractor reads). Must return one of `members` (the unit's spans, in order, with their tags and `scores`) -- or a copy of one with its `primary` set, when the unit's label is decided across members (e.g. a vote over their `scores`). Without it the grouper's own choice stands (the engine's default grouper: the first member, often a heading -- a weak label).

### `Extractor: (unit: 'Unit', *, source_doc_id: 'str') -> 'Awaitable[UnitExtraction]'`

One unit -> the domain's typed records (required; the domain-specific step).

### `RecordWriter: (source_doc_id: 'str', extractions: 'Sequence[UnitExtraction]') -> 'Awaitable[None]'`

Optional: persist a document's extractions. The engine default writes them with `kg_write`.

### `BoundaryDiscoverer: (**kwargs)`

The semantic boundary-discovery seam: decide chunk boundaries as a partition of item-index spans.

## Type aliases

### `LayoutKind = typing.Literal['title', 'heading', 'paragraph', 'list_item', 'table', 'caption', 'footnote', 'page_header', 'page_footer', 'code', 'formula', 'form', 'other']`

### `SpanKind = typing.Literal['title', 'heading', 'paragraph', 'list_item', 'table', 'table_row', 'caption', 'footnote', 'page_header', 'page_footer', 'code', 'formula', 'form', 'other']`

### `BoundaryDecider = typing.Callable[[list[str]], typing.Awaitable[list[bool]]]`

### `TableMode = typing.Literal['auto', 'record', 'block']`

### `DocumentHook = typing.Callable[[typing.Any, typing.Any, list], typing.Awaitable[typing.Any]]`

## Constants

### `NOT_NULL`

`NOT_NULL`: the sentinel for a `kg_edges` filter value meaning `<field> IS NOT NULL` (instead of an equality/membership match), e.g. `edge_where={"dimension": NOT_NULL}`.

## Functions

### `open_workspace(config: 'EngineConfig', *, corpus: 'str', reset: 'bool' = False, create: 'bool' = True) -> 'WorkspaceHandle'`

Resolve (and cache) the workspace for `corpus` (the backend database name) from `config`. Ensures the schema: the neutral engine types, plus `config.pack`'s declared types when set. Raises `RuntimeError` on a database whose `Span` type still has the pre-ING-8d field names (migrate it with `migrate_span_fields`). Returns an opaque `WorkspaceHandle`. `reset=True` drops + recreates the database (test/clean-slate) and bypasses the cache. A missing database is created (logged): that is how a new corpus is provisioned. `create=False` raises `LookupError` instead (PS-27) -- use it wherever the corpus must already exist, so a wrong name fails loudly rather than opening an empty graph.

One handle per store and corpus is cached for the process. An equal `config` returns it; a different one (new models, options, credentials or pack) returns a new handle that replaces it from then on, so a configuration change takes effect without a restart (PS-16). The new handle reuses the store when the store settings and the pack are unchanged, and the query embedder when the embedding profile is unchanged; a call still holding the old handle finishes on the old config.

### `drop_workspace(config: 'EngineConfig', *, corpus: 'str') -> 'bool'`

Drop the database `corpus` (PS-37): everything in it, the retrieval index and the knowledge graph, is deleted and cannot be recovered. Returns True if it existed, False if there was nothing to drop. A cached handle for it is closed and forgotten, so a later `open_workspace` starts fresh. A missing or blank `corpus` raises `ValueError`: the name is always explicit, never a default database.

### `migrate_span_fields(config: 'EngineConfig', *, corpus: 'str', batch: 'int' = 5000, progress: 'Optional[Callable[[int, int], None]]' = None) -> 'int'`

Migrate the database `corpus` from the pre-0.2.0 `Span` field names (`contract_id`, `function`, `functions`) to the generic ones (`document_id`, `primary_tag`, `tags`), the database `open_workspace` refuses until this has run (PS-35). Values are copied in batches of `batch` and the old properties dropped; `progress(done, total)` is called after each batch. Idempotent: returns the number of records moved, 0 when already migrated. The database must exist (`LookupError` otherwise; it is never created), and a missing or blank `corpus` raises `ValueError`. Afterwards the schema is checked, so a database this returns for opens with `open_workspace`.

### `temporary_workspace(config: 'EngineConfig', *, prefix: 'str' = 'tmp') -> 'Iterator[WorkspaceHandle]'`

A workspace on a fresh database, `<prefix>_<12 hex>`, dropped when the block exits, whether it succeeds or raises (PS-37). For a probe or a measurement whose database must not outlive it: `with temporary_workspace(config, prefix="probe") as ws: ...`.

### `pack_store(ws: 'WorkspaceHandle', cls: 'Callable[..., _T]', *args: 'Any', **kwargs: 'Any') -> '_T'`

Build a pack's store extension (or any object that wraps the workspace store) over the workspace's store: `cls(<store>, *args, **kwargs)`, e.g. `pack_store(ws, MyPackStore)`. The store it receives implements the engine's `Store` protocol (`kg_read` / `kg_write` / `kg_edges` / `kg_count` / `kg_delete` / `kg_update` and the rest); the workspace keeps the store itself private, so this is the one way a product hands it to a pack.

### `use_workspace_models(ws: 'Any') -> 'Iterator[None]'`

Resolve every model role through `ws`'s `EngineConfig.models` inside the block (PS-14), ahead of the `RAG_MODEL_*` environment. The engine's own entry points that take a workspace (`aingest`, the invokers) do this for you; use it around engine calls that take no workspace, such as `parse_document_bytes` (OCR) or a `default_chunk_discoverer` run, when the workspace overrides those roles. Per call, never shared: concurrent blocks for different workspaces each see their own models.

### `adeploy_model_server(spec: 'ModelServerSpec', *, wait: 'bool' = True, timeout_s: 'float' = 1500, poll_s: 'float' = 15, progress: 'Callable[[str], object]' = print) -> 'ModelServer'`

Deploy (or redeploy) the server described by `spec` on Modal, register its model profile, and (with `wait`) wait for it to be ready. Returns the `ModelServer`; use `server.model_id` as the model. Raises `RuntimeError` when the API-key variable is unset (nothing is deployed). The server runs (and bills) until `stop_model_server`, or until Modal scales it down after 10 idle minutes. Stopping discards the server's snapshots, so the next deploy pays snapshot-building boots again (ADR-0132).

### `amodel_server(spec: 'ModelServerSpec') -> 'ModelServer'`

Reconnect to a server deployed earlier (by this process or another) without redeploying: look up its URL and register its model profile. `spec` must match the deployed server (its name and served model). Raises `ModelServerNotDeployed` when no server has that name.

### `await_model_server(server: 'ModelServer', *, timeout_s: 'float' = 1500, poll_s: 'float' = 15, progress: 'Callable[[str], object]' = print) -> 'ModelServer'`

Wait until the server answers `/health`; progress as `i/N`. A cold start restored from the server's snapshot is ready ~13 s after its container starts; a boot that builds a snapshot (the first few, and now and then another) takes 4-9 minutes (ADR-0132). Raises `TimeoutError` after `timeout_s`.

### `stop_model_server(name: 'str') -> 'None'`

Stop the Modal app `name` (stops billing); its URL stops answering.

### `amodel_server_status(spec: 'ModelServerSpec') -> 'ModelServerStatus'`

Look, without deploying or waking anything: is the server deployed, is a container running (billing), does it answer `/health`, and are its model weights cached. `/health` is asked only of a running container: a request to a scaled-down server would start a cold start.

### `astop_model_server(name: 'str') -> 'None'`

`stop_model_server` for async code.

### `publish(slug: 'str', *, root: 'Optional[Path]' = None) -> 'Path'`

Author `slug` and write its manifest to `<root>/<slug>.json` (default: the shared root).

### `publish_all(*, root: 'Optional[Path]' = None) -> 'list[Path]'`

Author and write every specified manifest into the root. Returns the written paths.

### `register_decision_profile(profile: 'DecisionModelProfile') -> 'DecisionModelProfile'`

PS-38 (G12): add (or replace) the decision-model profile for `profile.model_id`, process-wide, so that id (named by `RAG_DECISION_MODEL` or the `jev_decision` input `model`) reaches its own endpoint and key from now on: an on-premises Laya server, or any server speaking the Decisions API, is configuration.

### `register_model_profile(profile: 'ModelProfile') -> 'ModelProfile'`

Add (or replace) the profile for `profile.model_id`, process-wide, so that model id resolves to it from now on (PS-19: a deployed server registers its own profile).

### `agent_chat_model(model: 'Optional[str]' = None, *, temperature: 'float' = 0.0, trace: 'bool' = True, **overrides: 'Any') -> 'BaseChatModel'`

The chat model for an agent: `model` is an engine model id (a registered profile's key, or any served id); `None` takes the GENERAL role's model. `overrides` are the chat model's own settings (`timeout`, `max_retries`, `max_tokens`, `callbacks`, ...). Retries are the caller's to place: an agent harness usually keeps them in its middleware and passes `max_retries=0`. `trace=False` keeps the metering and emits no Langfuse generation, for a caller that emits its own (so a call is never traced twice).

### `query_embedder(*, post: 'Callable[..., dict]' = post_json) -> 'Any'`

The query-side embedder: the remote A100 `/embed` adapter when `STACK_URL` is set, else the local in-process `BGEM3Embedder` (dev/default). One env (`STACK_URL`) moves the query's embed onto the A100.

### `ainvoke_subgraph(name: 'str', inputs: 'dict', *, resources: 'WorkspaceHandle') -> 'Any'`

Invoke a subgraph-kind capability by name over the workspace, inside a trace span. The implementation is resolved lazily from the manifest `impl_ref` (no central adapter dict). Retry/dead-letter comes from the LangGraph scaffold the subgraph is built on; usage is captured by the caller's `measure_usage()` (EP-API-5).

### `invoke_model(name: 'str', inputs: 'dict', *, resources: 'WorkspaceHandle') -> 'Any'`

Invoke a model-kind capability by name (SYNCHRONOUSLY). Validated against the ARD catalog, then resolved via `impl_ref` and dispatched -- the SAME path the ingestion pipeline routes through (one production path, no second hand-built fleet). `resources` is accepted for API uniformity but model capabilities are store-independent. Usage is the caller's `measure_usage()` scope (EP-API-5). A model impl may be async (I/O-bound, e.g. an LLM-backed cap) -- those cannot be invoked here; call `ainvoke_model` instead (we refuse rather than silently return an un-awaited coroutine).

### `ainvoke_model(name: 'str', inputs: 'dict', *, resources: 'WorkspaceHandle', sem: 'asyncio.Semaphore | None' = None) -> 'Any'`

Invoke a model-kind capability by name, ASYNCHRONOUSLY -- the async surface for model caps (the subgraph legs already have `ainvoke_subgraph`). A model impl is one of two shapes, and this routes each honestly: * SYNC (CPU-bound local inference -- a classifier/XGBoost fleet): run OFF the event loop in a worker thread (`asyncio.to_thread`), so a big batch never blocks the loop; * ASYNC (I/O-bound -- an LLM-backed cap calling OpenRouter or a local vLLM client): AWAITED directly, so the I/O concurrency is real (not a thread wrapping a blocking call). `sem` (an `asyncio.Semaphore`) bounds total in-flight work when a caller fans out a batch -- the same backpressure the ingestion pipeline applies. Usage is the caller's `measure_usage()` scope (EP-API-5).

### `capability_index() -> 'dict[str, dict]'`

Public discovery index: `{slug: {kind, description}}` for every catalogued capability (the 'cards').

### `discover(query: 'str', *, resources: 'WorkspaceHandle', kind: 'Optional[str]' = None, k: 'int' = 8) -> 'list[Discovered]'`

Rank the live ARD catalog by semantic match to `query`; return the top `k` (optionally filtered to one `kind`: `subgraph` / `model` / `function` / `agent_skill` / `mcp_tool`). Embedding-based, via the workspace's query embedder (BGE-M3, the same space retrieval uses). Returns `[]` when the (filtered) catalog is empty; raises `RuntimeError` if the workspace has no query embedder available (discovery needs one).

### `kg_read(ws: 'WorkspaceHandle', node_type: 'str', *, where: 'Optional[dict]' = None, fields: 'Optional[list]' = None, distinct: 'Optional[str]' = None, order_by: 'Optional[str]' = None, limit: 'Optional[int]' = None, key_range: 'Optional[tuple]' = None) -> 'list[dict]'`

Read typed nodes of `node_type` from the workspace (see `Store.kg_read`). Each `where` value is a scalar (equality), a list (membership; an empty list is scope-to-nothing -> `[]`), `NOT_NULL` (presence) or a `Range` (comparisons, on any number of fields: `{"weight": Range(ge=180, le=220), "tensile": Range(gt=400)}`); all filters are AND-ed and evaluated in the store. `key_range=(field, lo, hi)` is the half-open shorthand (`lo <= field < hi`). Then projection, distinct, order and limit.

### `kg_write(ws: 'WorkspaceHandle', nodes: 'list', edges: 'Any' = ()) -> 'None'`

Upsert typed `nodes` + create typed `edges` in one transaction (see `Store.kg_write`). `nodes`/`edges` are `KgNode`/`KgEdge` (from `rag_wright.api`); the store encodes each field per its pack-declared type. A node type the workspace's schema does not declare fails the write.

### `kg_edges(ws: 'WorkspaceHandle', from_type: 'Optional[str]' = None, *, where: 'Optional[dict]' = None, key_range: 'Optional[tuple]' = None, direction: 'str' = 'out', edge_type: 'Optional[str]' = None, edge_where: 'Optional[dict]' = None, target_where: 'Optional[dict]' = None, select: 'dict') -> 'list[dict]'`

Generic edge TRAVERSAL over the workspace (see `Store.kg_edges`): node-start out/in MATCH (by `where` equality/membership or an id-prefix `key_range`) or a direct edge scan; `select` projects `c.`/`e.`/`v.` expressions. The engine's relational/graph primitive on the API, so a domain's graph query never touches `ws._store`. (`NOT_NULL` from `rag_wright.api` is the presence filter.)

### `kg_count(ws: 'WorkspaceHandle', type_name: 'str', *, where: 'Optional[dict]' = None, key_range: 'Optional[tuple]' = None) -> 'int'`

The number of vertices or edges of `type_name` matching `where` / `key_range` (see `Store.kg_count`); `where` as in `kg_read`, `Range` values included.

### `kg_delete(ws: 'WorkspaceHandle', type_name: 'str', *, where: 'Optional[dict]' = None, key_range: 'Optional[tuple]' = None) -> 'int'`

Delete the matching vertices (with their edges) or edges of `type_name`; all of them when `where` and `key_range` are both None. Returns the number deleted (see `Store.kg_delete`).

### `kg_update(ws: 'WorkspaceHandle', type_name: 'str', *, set: 'dict', where: 'Optional[dict]' = None, key_range: 'Optional[tuple]' = None) -> 'int'`

Set fields on the matching vertices or edges of `type_name`, changing only rows where a set field differs; returns the number changed (see `Store.kg_update`).

### `entities_by_name(ws: 'WorkspaceHandle', name: 'str') -> 'list[dict]'`

Resolve an entity NAME to every entity node it matches: `[{entity_id, name, entity_type}]` (the engine owns the surface-form normalization, so variants collapse to one id). One name can match several nodes (a resolved node + an unlinked ref sharing a clustering key) -- all are returned. `entity_id` is exactly the `start_entity_id` a graph traversal takes. The engine's generic entity-lookup primitive on the API.

### `span_positions(ws: 'WorkspaceHandle', document: 'str') -> 'list[dict]'`

Every span of `document` with its position provenance, ordered by document position. Each row: `span_id`, `parent_chunk_id`, `span_index`, `text`, `primary_tag` (the span tagger's primary tag, "" if untagged), `document_id`, `doc_start` / `doc_end` (document-absolute offsets), `pages`, and `bbox` DECODED to a `(l, t, r, b)` tuple or None. The engine MECHANISM behind a product's citation/highlight types -- the product wraps these rows into its own presentation type (e.g. `SpanLocation`).

### `span_search(ws: 'WorkspaceHandle', query: 'str', *, k: 'int' = 10, documents: 'Optional[list]' = None, tag: 'Optional[str]' = None, dense_floor: 'int' = 3) -> 'list'`

Hybrid search over the workspace's span index (every ingested span, embedded dense and sparse): the two legs fused by Reciprocal Rank Fusion in the store, with the top `dense_floor` pure-dense spans guaranteed a place, so a strong semantic match a common-token query would push out of the fused pool is kept (0 turns it off). Returns up to `k` `SpanHit`s, best first, each citation-ready: `span_id`, `text`, `document_id`, `doc_start` / `doc_end`, `pages`, `bbox`, `primary_tag`, `rank`, and `found_by` (`"hybrid"` and/or `"dense"`). `documents` scopes it to those source documents in the store (`None` = all, `[]` = nothing); `tag` keeps one primary tag. No reranking: compose `BGEReranker` over the hits if you want one. Raises `RuntimeError` when the workspace has no embedder.

### `aspan_search(ws: 'WorkspaceHandle', query: 'str', *, k: 'int' = 10, documents: 'Optional[list]' = None, tag: 'Optional[str]' = None, dense_floor: 'int' = 3) -> 'list'`

`span_search` off the event loop (the query embedding and the store calls block), for an async caller.

### `document_of(entity_id: 'str') -> 'str'`

The source-document id embedded in a chunk/span/unit id (`<source_doc_id>:<idx>:<hash>` -> the first, delimiter-safe segment). Empty in -> empty out.

### `decode_bbox(raw: 'Optional[str]') -> 'Optional[tuple]'`

Decode the engine's best-effort bounding box (stored as a JSON `[l,t,r,b]` string) to a `(l, t, r, b)` tuple, or None. The single canonical decoder (retires the product seam's copy).

### `source_document(document_id: 'str', *, text: 'str') -> 'Any'`

A text-only `SourceDocument` (`source_doc_id`, `text`) to pass as the `document` input of an ingestion capability that takes one (the reference pack's `contract_ingestion_pipeline`). `build_ingestion` reads files itself and does not take a `SourceDocument`. The id should be a canonical, delimiter-safe source-doc id.

### `parse_document(document_id: 'str', path: 'Any', *, cache_dir: 'Any', metadata: 'dict | None' = None, include_hidden_sheets: 'bool' = True, tuning: 'Any' = None) -> 'Any'`

Docling-parse the file at `path` ONCE (content-hash gated + cached under `cache_dir`) into a structure-bearing `SourceDocument` -- `.parsed` carries the `DoclingDocument` so the chunker's structural pass fires on real headings, and `.text` holds the flattened text. Pass the result as the `document` input of an ingestion capability that takes one (the reference pack's `contract_ingestion_pipeline`), or to `table_rows`; `build_ingestion` parses its source paths itself through the same cached parse. The docling parse blocks; use `aparse_document` on an event loop. A spreadsheet's hidden sheets are ingested unless `include_hidden_sheets=False` (then listed in `.skipped_hidden_sheets`). Files embedded in an Office package are extracted as `.embedded` children linked to their records (`tuning.identifier` sets the identifier rule).

### `aparse_document(document_id: 'str', path: 'Any', *, cache_dir: 'Any', metadata: 'dict | None' = None, include_hidden_sheets: 'bool' = True, tuning: 'Any' = None) -> 'Any'`

The async, deadline-bounded twin of `parse_document` (ADR-0057): runs the docling parse off the event loop so a hand-built async ingest can parse a document into a structure-bearing `SourceDocument` without blocking.

### `parse_document_bytes(document_id: 'str', name: 'str', data: 'bytes', *, cache_dir: 'Any', metadata: 'dict | None' = None, include_hidden_sheets: 'bool' = True, tuning: 'Any' = None) -> 'Any'`

`parse_document` from in-memory BYTES (an upload read from object storage), no temp file. `name` is the file name; its extension picks the format (`.pdf`, `.docx`, `.xlsx`, `.md`, ...). The parse is content-hash gated and cached under `cache_dir` exactly like the path form, and returns the same `SourceDocument`. The docling parse blocks; use `aparse_document_bytes` on an event loop.

### `pdf_page_count(source: 'Any') -> 'int'`

The number of pages in a PDF (a path or the PDF's bytes), read under the engine's PDF lock and closed before it is released (PS-31, ADR-0138): pdfium is not thread-safe, so a product must not call pypdfium2 itself beside the engine's parses. Raises `ValueError` when the PDF cannot be read.

### `aparse_document_bytes(document_id: 'str', name: 'str', data: 'bytes', *, cache_dir: 'Any', metadata: 'dict | None' = None, include_hidden_sheets: 'bool' = True, tuning: 'Any' = None) -> 'Any'`

The async, deadline-bounded twin of `parse_document_bytes` (ADR-0057): runs the docling parse off the event loop.

### `find_blank_pages_with_text(cache_dir: 'Any', sources: 'Iterable[Source]' = (), *, progress: 'Optional[Callable[[int, int], None]]' = None) -> 'list[BlankPageText]'`

Audit the parse cache under `cache_dir` (the one given to `parse_document`, or to `aingest`, which keeps its parses in `parsed/`; searched recursively) for text on blank pages. `sources` are the documents' PDFs, as paths or `(name, bytes)`; files embedded in a parsed package are stored in the cache and audited without being passed. A source that is not a PDF, or has no cached parse, is skipped. `progress(done, total)` is called after each PDF. Raises `FileNotFoundError` when `cache_dir` does not exist.

### `agenerate_answer(query: 'str', evidence: 'list[EvidenceItem]', *, ws: 'Any', guidance: 'Optional[str]' = None) -> 'GeneratedAnswer'`

A grounded, cited answer to `query` over `evidence`, or an abstention. Empty evidence abstains without a model call; a citation not in the evidence is dropped, and an answer left with no valid citation becomes an abstention (no claim without a citation). `answer_kind` says whether the evidence fully supported the answer (`answered`), only partly (`partial`) or not at all (`abstained`). `guidance` adds your domain's wording to the domain-neutral method.

### `ajudge_spans(spans: 'list[tuple[str, list[tuple[str, str]]]]', condition: 'Condition', *, ws: 'Any', max_concurrency: 'int' = 8, guidance: 'Optional[str]' = None) -> 'list[RelevanceVerdict]'`

Judge whether each span addresses `condition`, concurrently, one verdict per span in order. Each span is `(text, matched)`, where `matched` lists `(property, value)` pairs already detected on the span (context for the judge, not proof; pass `[]` when there are none). Every verdict is in the closed `Relevance` vocabulary: an unreadable judgment, or one that timed out, is `uncertain` (never a fabricated `not_relevant`). `guidance` adds your domain's wording (what its categories look like) to the domain-neutral method.

### `measure_usage() -> 'Iterator[UsageTotals]'`

Accumulate the model usage of every engine call made inside the block; read the returned `UsageTotals` after it. Nesting is additive, so a task-level scope totals everything while an inner per-call scope attributes its slice. Capturing is opt-in: with no active scope, the engine records usage nowhere (zero overhead).

### `record_usage(model: 'str', *, input_tokens: 'int' = 0, output_tokens: 'int' = 0, cost: 'Optional[float]' = None, latency_ms: 'Optional[float]' = None) -> 'None'`

Record one model call into every active scope (a no-op when none is active, so it is safe to call on every model call regardless of tracing). `cost=None` means the backend surfaced no cost (counted as `calls_without_cost`, never as $0). `model` is the engine model-id string, keyed consistently across paths.

### `traced_run(*, document_id: 'Optional[str]' = None, job_id: 'Optional[str]' = None, name: 'Optional[str]' = None, metadata: 'Optional[dict[str, Any]]' = None) -> 'Iterator[None]'`

Group every generation emitted inside the block under one trace/session -- the caller's correlation id (job/document). A no-op unless tracing is on. Flushes on exit so a short-lived run's spans are sent.

### `traced_step(name: 'str', *, metadata: 'Optional[dict[str, Any]]' = None) -> 'Iterator[None]'`

Time a NON-generation sub-step (e.g. retrieval: ArcadeDB + embedding + rerank) as its OWN Langfuse span, so its duration is separable from the generation in the same trace -- the retrieval/generation split engine issue 0048 asked for. No-op unless tracing is on. (Distinct from `subgraphs.observability.business_span`, which is an OTel-ambient span that no-ops under a Langfuse-only setup -- this one emits to Langfuse.)

### `tracing_on() -> 'bool'`

_(no docstring)_

### `trace_level() -> 'str'`

_(no docstring)_

### `start_generation(*, model: 'str', input: 'Any' = None, label: 'Optional[str]' = None, role: 'Optional[str]' = None, stage: 'Optional[str]' = None, metadata: 'Optional[dict[str, Any]]' = None) -> 'Any'`

Open a Langfuse generation AT THE CALL START and return the observation (or None when tracing is off). Its `start_time` is the moment this is called, so pairing it with `finish_generation` after the call makes the span's OWN duration the real wall-clock latency. This matters because langfuse v4 has no way to back-date a start: a post-hoc emit (open + immediately end) reports a ~0s duration and the real figure survives only in metadata -- the defect engine issue 0048 hit (Langfuse's latency column read 0 for `astream_text`/`span-relevance`). `input` is captured only at `verbose`; label/role/stage go into metadata. Document/job grouping comes from the ambient `traced_run`.

### `finish_generation(gen: 'Any', *, output: 'Any' = None, usage: 'Optional[dict[str, int]]' = None, cost: 'Optional[float]' = None, latency_ms: 'Optional[float]' = None, completion_start_time: 'Any' = None, metadata: 'Optional[dict[str, Any]]' = None) -> 'None'`

Attach a completed call's results to a generation opened by `start_generation` and END it (end_time = now), so the observation's duration is the true latency. No-op when `gen` is None (tracing off). `output` captured only at `verbose`; `usage` = {"input": n, "output": n}; `cost` is the provider's ACTUAL total USD (OpenRouter pass-through, not an engine price table) -> `cost_details` (None when the backend omits it, e.g. self-hosted vLLM, so Langfuse prices from its own table). `completion_start_time` (time to first token) lets Langfuse split queue+prefill from decode -- the attribution engine issue 0048 asked for. `latency_ms` is also kept in metadata (redundant with the now-correct span duration, but exact).

### `flush_tracing() -> 'None'`

PS-32: hand the engine client's buffered spans to the exporter now (a caller emitting its own generations wants a stalled run visible while it stalls). A no-op when tracing is off; never raises.

### `register_capability(manifest: 'CapabilityManifest') -> 'None'`

Register (or replace) one capability in the runtime ARD catalog. A product calls this for each of its domain capabilities (with an `impl_ref`); the invoker then resolves it by name with zero engine edits. The catalog starts EMPTY: the engine's own capabilities (e.g. `jev_decision`, `generation`) are registered the same way when a product uses them (their manifests are `capabilities.manifests.engine_capabilities()`).

### `load_reference_pack() -> 'None'`

Register the engine's reference pack into the runtime catalog -- the opt-in worked example: the contracts and compliance packs plus the engine capabilities they use (the engine's own test suite loads it; a downstream product does NOT, registering its own capabilities instead).

### `reference_pack() -> 'tuple[CapabilityManifest, ...]'`

The engine's committed REFERENCE PACK: the engine capabilities it uses + the contract/compliance worked example's manifests (`rag_wright.packs.contracts.pack` + `rag_wright.packs.compliance.pack`). Opt-in.

### `load_pack(module_name: 'str') -> 'None'`

ING-8b: load a capability PACK by module name -- the module's `register()` adds its canonical slugs and registers its manifests (+ any engine capabilities it builds on). A product's own pack plugs in the same way.

### `engine_capabilities() -> 'tuple[CapabilityManifest, ...]'`

ING-8b: the manifests of the engine's GENERIC capabilities (generation, the RLM skills, vision-to-text, the decision model, span relevance judgment). Opt-in, like every capability: the engine ships an empty catalog.

### `register_canonical_slugs(slugs) -> 'None'`

Add a pack's canonical capability slugs to the registration whitelist (idempotent).

### `canonical_capability_slugs() -> 'frozenset[str]'`

The current canonical capability slugs: the engine's plus those of every loaded pack.

### `check_tiling(chunk_id: 'str', text: 'str', spans: 'Sequence[Span]') -> 'None'`

A segmenter's spans must tile `text` in order, byte-faithfully, under the `span_id` scheme.

### `check_units(spans: 'Sequence[Span]', units: 'Sequence[Unit]') -> 'None'`

A grouper's units must use known spans, each at most once, in document order, indexed 0..k-1.

### `check_extraction(unit: 'Unit', extraction: 'UnitExtraction') -> 'None'`

Provenance (FR-S.4), wherever the facts live -- on nodes or on edges (ADR-0124, ING-4c): every node or edge that carries a `span_id` must cite a span of its unit, every `confidence` must be a `ConfidenceTag`, and a non-empty extraction must cite at least once. Nodes without a `span_id` are shared vocabulary (value or taxonomy nodes) and are allowed beside a cited fact.

### `build_ingestion(extractor: 'Extractor', *, segmenter: 'Optional[Segmenter]' = None, span_tagger: 'Optional[SpanTagger]' = None, unit_grouper: 'Optional[UnitGrouper]' = None, boundary_decider: 'Optional[BoundaryDecider]' = None, writer: 'Optional[RecordWriter]' = None, tuning: 'Optional[IngestionTuning]' = None, embedder: 'Any' = None, chunk_model: 'Optional[str]' = None, progress: 'Callable[[str], Any]' = functools.partial(print, flush=True), document_hook: 'Optional[DocumentHook]' = None, unit_representative: 'Optional[UnitRepresentative]' = None, chunk_discoverer: 'Any' = None) -> 'IngestionPipeline'`

The engine's generic ingestion pipeline: pass your `extractor` (a `Unit` -> `UnitExtraction`) and override any other hook you need; `tuning` sets the thresholds of the default hooks. `embedder` (an `encode_batch` object) defaults to the workspace's ingest embedder; `chunk_model` is used only to refine an over-cap section. `document_hook(ws, source_document, chunks)` is awaited once per document after its records are written (e.g. a domain's entity graph); its return value is ignored. `boundary_decider` (a `BoundaryDecider`: candidate line texts -> "starts a new unit?" per text) is handed to the unit grouper to settle the boundaries its rules are unsure of; None = the grouper's own rules only. `unit_representative` (a `UnitRepresentative`: a unit's member spans -> the one that represents it) sets each unit's `anchor` and leading tag after grouping -- e.g. the operative sentence rather than a heading; None = the grouper's own choice. `chunk_discoverer` (a `BoundaryDiscoverer`) sets the chunk-boundary rule; None = the engine default (structural boundaries, `chunk_model` refining only over-cap sections), and `default_chunk_discoverer(guidance=...)` is that default with your domain's wording. Returns an `IngestionPipeline`; run it with `await pipeline.aingest(ws, sources, cache_dir=...)`.

### `default_chunk_discoverer(chunk_model: 'Optional[str]' = None, *, guidance: 'Optional[str]' = None) -> 'Any'`

PS-R5b: the engine's default chunk-boundary rule (structural boundaries first; `chunk_model` refines only an over-cap section, through a domain-neutral prompt) with your domain's `guidance` added to that prompt (e.g. what a coherent unit is in your documents). Pass it as `build_ingestion(chunk_discoverer=...)`.

### `aevaluate_ingestion(sources: 'Sequence[Union[str, Path, IngestSource]]', *, cache_dir: 'Union[str, Path]', tuning: 'Optional[IngestionTuning]' = None, segmenter: 'Optional[Segmenter]' = None, unit_grouper: 'Optional[UnitGrouper]' = None, table_labels: 'Optional[list[dict[str, Any]]]' = None, span_tagger: 'Optional[SpanTagger]' = None, unit_representative: 'Optional[UnitRepresentative]' = None, unit_labels: 'Optional[dict[str, list[dict[str, str]]]]' = None, progress: 'Optional[Callable[[str], Any]]' = None) -> 'IngestionEvaluation'`

`evaluate_ingestion` for a running event loop (a web backend, a worker): the same checks and result, with documents run `tuning.document_concurrency` at a time (parse and segment off the loop) and, when `progress` is given, `[evaluate] start N=<n>`, `[evaluate] <i>/<n> <document> ok|error: <reason>` and `[evaluate] done passed=<bool> failures=<k>` lines. Results keep the order of `sources`; `IngestionEvaluation.to_dict()` is the JSON-ready result.

### `evaluate_ingestion(sources: 'Sequence[Union[str, Path, IngestSource]]', *, cache_dir: 'Union[str, Path]', tuning: 'Optional[IngestionTuning]' = None, segmenter: 'Optional[Segmenter]' = None, unit_grouper: 'Optional[UnitGrouper]' = None, table_labels: 'Optional[list[dict[str, Any]]]' = None, span_tagger: 'Optional[SpanTagger]' = None, unit_representative: 'Optional[UnitRepresentative]' = None, unit_labels: 'Optional[dict[str, list[dict[str, str]]]]' = None) -> 'IngestionEvaluation'`

Measure the ingestion hooks' structural fidelity on YOUR sample documents, before trusting them. Runs parse, chunk, segment and group only (no store, no model calls) with the default hooks or the `segmenter` / `unit_grouper` you pass, under `tuning`. Checks: spans tile the text, table rows stay whole, spans respect layout items, no bare-heading spans, headings start units, tables stay whole (or split per row for a record table), no page furniture in units, full coverage, units within the cap. `table_labels` (`[{"pattern": <regex on the header row>, "label": "record" | "block"}]`, first match wins) also checks each table's mode. Returns an `IngestionEvaluation`; tune `IngestionTuning` until `passed`.

Labelling (PS-R4): pass your `span_tagger` (and any `unit_representative`) with `unit_labels`, the gold `{<document file name>: [{"text": <a snippet that occurs in exactly one unit>, "label": <expected label, "" for none>}]}`, and `labels` (a `LabelEvaluation`) reports each gold unit's label against it: accuracy, per-label results, confusion pairs. The tagger is yours, so it may call models; nothing else does.

Inside a running event loop (a web backend), await `aevaluate_ingestion` instead.

### `load_gold_set(source: 'Union[str, Path, IngestSource]', mapping: 'GoldMapping', *, name: 'str', version: 'str', provenance: 'Provenance' = 'hand', local_only: 'bool' = True, sheet: 'Optional[str]' = None, description: 'str' = '') -> 'GoldSet'`

Read a gold set from an expert's CSV, XLSX or JSONL file (a path, or an `IngestSource` holding an upload's bytes) through `mapping`. `provenance` is every item's unless the mapping names a provenance column; `sheet` reads one XLSX sheet (hidden ones too). Raises `GoldImportError` on anything that would make the gold wrong.

### `aload_gold_set(source: 'Union[str, Path, IngestSource]', mapping: 'GoldMapping', *, name: 'str', version: 'str', provenance: 'Provenance' = 'hand', local_only: 'bool' = True, sheet: 'Optional[str]' = None, description: 'str' = '') -> 'GoldSet'`

`load_gold_set` off the event loop, for a web backend.

### `score_classification(gold: 'Gold', predictions: 'Mapping[str, Any]', *, floor: 'float', k: 'int' = 1, top1_floor: 'Optional[float]' = None, exclude: 'Collection[str]' = (), name: 'str' = 'classification') -> 'ScorerResult'`

Scorer 1. `expected`: a label, or several (multi-label). A prediction: a label, a ranked list or `{label: score}`. Gate: every class's recall@k reaches `floor` (classes in `exclude`, e.g. a no-label class, are reported but not gated) and, with `top1_floor`, top-1 accuracy reaches it. Diagnostics: per-class recall@k, their mean, top-1 accuracy, tags per item (the precision cost of k), confusion pairs (gold vs top prediction), errors.

### `score_candidate_choice(gold: 'Gold', predictions: 'Mapping[str, Any]', *, recall: 'float', precision: 'float', coverage: 'float', match: 'Callable[[Any, Any], bool]' = lenient_match, name: 'str' = 'candidate_choice') -> 'ScorerResult'`

Scorer 8. `expected`: `{"values": [[key, value], ...], "ignore": [value, ...]}` (`ignore`: values the gold cannot settle, dropped from the output before scoring), or just the list of `[key, value]`. A prediction: `{"candidates": [value, ...], "values": [[key, value], ...]}`. Gate: coverage (expected values found among the candidates), value-level recall (an expected value chosen under its key) and precision (a chosen value that is expected under that key). Diagnostics: the counts, values per item, coverage misses, errors.

### `lenient_match(a: 'Any', b: 'Any') -> 'bool'`

Normalised (spaces collapsed, case and edge punctuation ignored), either one containing the other.

### `score_retrieval(gold: 'Gold', predictions: 'Mapping[str, Any]', *, k: 'int', recall: 'float', relevant_grade: 'int' = 1, ndcg_k: 'int' = 10, also_recall_at: 'Sequence[int]' = (), name: 'str' = 'retrieval') -> 'ScorerResult'`

Scorer 2. `expected`: the relevant ids, or `{id: grade}`. A prediction: ids, best first. Gate: mean recall@k, relevant = grade >= `relevant_grade`. Diagnostics: graded nDCG@`ndcg_k` (never gated), recall at each of `also_recall_at`, per-item recall@k. An item with nothing relevant is left out of the means and listed in `no_relevant` (recall is undefined, not perfect).

### `score_relational(gold: 'Gold', predictions: 'Mapping[str, Any]', *, recall: 'float', k: 'Optional[int]' = None, by: 'Optional[str]' = None, name: 'str' = 'relational') -> 'ScorerResult'`

Scorer 6. `expected`: the answer set (ids). A prediction: the ids reached (`k` keeps only the first k of a ranked answer; None = all). Gate: mean recall over the answer sets. Diagnostics: the mean per value of the item metadata key `by` (e.g. hops), precision (reached ids that are answers; over-reach, never gated), the answers not reached. An item with an empty answer set is left out and listed in `no_answers`.

### `score_extraction(gold: 'Gold', predictions: 'Mapping[str, Any]', *, recall: 'float', precision: 'float', average: 'str' = 'micro', normalize: 'Optional[Callable[[Any], str]]' = None, match: 'Optional[Callable[[str, str], bool]]' = None, name: 'str' = 'extraction') -> 'ScorerResult'`

Scorer 3. `expected` and a prediction: the items (e.g. names, requirements). Items are normalised (`normalize`, default: spaces collapsed, case and edge punctuation ignored) and matched (`match`, default: either one containing the other); an item listed twice after normalising counts once. Gate: recall (expected items extracted) and precision (extracted items that are expected), APART; `average` "micro" pools the counts, "macro" averages per item (an item that extracted nothing has precision 0). Diagnostics: F1, misses, extras, recall by input length (up to four equal groups, shortest first). An item with no expected items is left out and listed in `no_gold`.

### `score_judgment(gold: 'Gold', predictions: 'Mapping[str, Any]', *, actionable: 'Any', precision: 'float', recall: 'float', by: 'Optional[str]' = None, score_label: 'Any' = None, name: 'str' = 'judgment') -> 'ScorerResult'`

Scorer 4. `expected`: the verdict. A prediction: the verdict, or `{"verdict": ..., "score": p}` with `p` the probability that the verdict is `score_label`. Gate: precision (alert fatigue) and recall (missed) of the `actionable` verdict, APART -- for a judge that refutes other outputs, recall is its error-catch rate. Diagnostics: accuracy, false-alarm rate (actionable called on a non-actionable item -- a judge's false refutes), per-class recall, the same rates per value of the metadata key `by` (e.g. provenance), and with `score_label` calibration: per score bin, how often the expected verdict is `score_label`. A missing verdict is an error: wrong, and never the actionable verdict.

### `score_generation(gold: 'Gold', predictions: 'Mapping[str, Any]', *, citation_recall: 'float', abstention: 'float', grounded: 'float', judgments: 'Optional[Mapping[str, bool]]' = None, name: 'str' = 'generation') -> 'ScorerResult'`

Scorer 5. `expected`: the gold answer texts (or `{"answers": [...]}`); none = the right response is to abstain. A prediction: the generated answer (`answer`, `citations` = evidence ids, `abstained` or `answer_kind`) with the `evidence` it was given (`{id: text}`). Gates: citation recall over the answerable items (gold answers whose normalised text is inside the cited evidence; an abstention recalls nothing), correct abstention (abstained iff nothing to answer), groundedness over the answers (citations that point at the given evidence; an answer with no citation is ungrounded). `judgments` (`{id: supported}`, from an LLM judge the caller ran) are reported, never gated. A missing prediction is an error: wrong, never an abstention.

### `score_ingestion(evaluation: "'IngestionEvaluation'", *, label_accuracy: 'Optional[float]' = None, name: 'str' = 'ingestion') -> 'ScorerResult'`

Scorer 7: an `evaluate_ingestion` result as a scorer result. Gate: every structural check held and, with `label_accuracy`, the unit-labelling accuracy reaches it. Diagnostics: the failures, the warnings, a line per document, the unlabelled tables and the labelling results (per label, confusions).

### `arun_eval(gold: 'GoldSet', task: 'Task', scorers: 'Sequence[Scorer]', *, repeats: 'int' = 1, concurrency: 'int' = 8, progress: 'Optional[Callable[[str], Any]]' = None, task_name: 'Optional[str]' = None, models: 'Sequence[str]' = (), langfuse: 'Optional[bool]' = None, langfuse_client: 'Any' = None, run_name: 'Optional[str]' = None) -> 'EvalReport'`

Run `task` on every item of `gold`, `repeats` times, `concurrency` jobs at a time, and score each repeat with `scorers` (`(gold, {item id: output}) -> ScorerResult`, e.g. `functools.partial(score_judgment, ...)`). `progress` receives `[eval] start N=<jobs> (...)`, `[eval] <i>/<N> <item id> r<k> ok|error: <reason>` and `[eval] done passed=<bool> errors=<n> flips=<n>`. `task_name` and `models` are recorded in the report. `langfuse` (None = `RAG_EVAL_BACKEND`, default local) runs it on the Langfuse backend, with `langfuse_client` (default: a client from the environment) and `run_name` (default: the task name and the time). Returns the `EvalReport`.

### `run_eval(gold: 'GoldSet', task: 'Task', scorers: 'Sequence[Scorer]', **kwargs: 'Any') -> 'EvalReport'`

`arun_eval` from synchronous code. Inside a running event loop (a web backend), await `arun_eval`.

### `rescore_eval(report: 'EvalReport', gold: 'GoldSet', scorers: 'Sequence[Scorer]') -> 'EvalReport'`

Score a saved report's outputs again (new scorers, thresholds or a corrected gold) without calling the task.

### `table_rows(source_document: 'Any') -> 'list[TableRow]'`

Every data row of every table in a parsed document (`parse_document` output), in document order.
