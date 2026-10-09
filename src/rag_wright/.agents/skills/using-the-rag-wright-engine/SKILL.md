---
name: using-the-rag-wright-engine
description: >-
  How a coding agent in a NEW product repo adopts the RAG_Wright engine: install it as a package, build the
  graphify grounding lanes over the installed engine, ground every engine API call before writing it, then follow
  the engine's domain-adaptation guide to configure the engine, author + ingest a domain .ttl pack, run entity
  resolution, register domain capabilities, and build the product seam. Use it at the start of a new-domain build
  (e.g. TexWright) and whenever calling the engine. Dependency is one-way: Product → Engine, never the reverse;
  the whole public surface is `rag_wright.api`.
---

# Using the RAG_Wright engine (consumer-side playbook)

You are building a **product** in its own repo on top of the RAG_Wright **engine** package. The engine is
open-core and domain-neutral; your product brings the domain. Hard rules:

- **You own your capabilities.** The engine's catalog starts empty; your seam registers everything you invoke, at
  startup (section 1). The contract/compliance capabilities are the engine's reference pack, not the engine.
- **Product → Engine, one way.** Never fork, edit, or reach into engine internals. If the engine needs a change,
  flag it upstream — don't work around it here.
- **The public surface is `rag_wright.api`; a pack also uses `rag_wright.pack_sdk`.** From `rag_wright.api` import
  `EngineConfig`/`StoreConfig` (`StoreConfig.from_env()` reads `ARCADEDB_*`)/`open_workspace`, the invokers
  (`ainvoke_subgraph`/`invoke_model`/`ainvoke_model`), discovery (`capability_index`/`discover`), the KG accessors
  (`kg_read` with `key_range`, `kg_write`, `kg_edges` with `NOT_NULL`, `kg_count`/`kg_delete`/`kg_update`,
  `entities_by_name`, `span_positions`), `pack_store(ws, cls)` (build your pack's store extension over the
  workspace's store; the store itself stays private), metering and tracing (`measure_usage`, `record_usage`,
  `traced_run`/`traced_step`), answers (`agenerate_answer`, `ajudge_spans` with `EvidenceItem`/`Condition`, the
  models from the workspace's roles, your domain wording via `guidance=`), `ModelRole`, pack loading
  (`register_capability`/`load_pack`/`engine_capabilities`/`load_reference_pack`), the document helpers
  (`parse_document`/`aparse_document`, `parse_document_bytes`/`aparse_document_bytes` for uploads,
  `source_document`/`table_rows`, `decode_bbox`, `document_of`), and the **ingestion surface**: `build_ingestion`
  (returns an `IngestionPipeline`; `await pipeline.aingest(ws, sources, cache_dir=...)` returns an
  `IngestionReport`), its hook contracts (`Segmenter`, `SpanTagger`, `UnitGrouper`, `BoundaryDecider`,
  `UnitRepresentative`, `BoundaryDiscoverer` with `default_chunk_discoverer(guidance=...)`, `Extractor`,
  `RecordWriter`, `DocumentHook`, checked by `check_tiling`/`check_units`/`check_extraction`), `IngestionTuning`,
  `IngestSource` (a `path`, or `data` + `name` for bytes; `table_mode`, `include_hidden_sheets`) and
  `evaluate_ingestion`. Your **pack's own code** (its capabilities, stores, graphs) also imports
  `rag_wright.pack_sdk`: identifiers and provenance, the model seam, the LangGraph scaffold, the `Store` protocol, the
  entity-graph building blocks (`build_graph_extraction`, `disambiguate`, `resolve_entities`, `EntityRules`,
  `GraphWriter`, `EntityRegistry`), reranking, embedding, parsing and chunking. Nothing deeper: anything else in the
  engine (`ArcadeDBStore`, `rag_wright.capabilities.*`, engine id formats) is internal and may change. The generated
  `docs/api/README.md` and `docs/api/pack_sdk.md` list both tiers. To **plan** over the engine for a task,
  `discover(task, resources=ws)` returns the best-matching capabilities (embedding-ranked); then invoke the top ones
  by slug.

## 1. Install

```sh
uv add 'rag-wright>=0.3.0'              # released-product mode (PyPI); see below for local co-development
```

Stand up the runtime prerequisites you provide: ArcadeDB (the store), a model provider (OpenRouter or self-hosted
vLLM), and — only for NER — `uv add 'rag-wright[ner]'` + `uv run python -m spacy download en_core_web_sm`.
Read the engine's **installation** + **configuration** docs for the exact `.env` and `EngineConfig` fields.

**Local co-development** (engine and product developed together on one machine): keep the floor and add an editable
source so engine changes are picked up immediately: `[tool.uv.sources] rag-wright = { path = "<engine checkout>",
editable = true }`. Check against the published release with `uv sync --no-sources` (never with `--locked`; never
commit the `uv.lock` it rewrites). Switch to the PyPI mode above once the product has CI, deploys or other developers.
The product-starter playbook (section 5) has both modes.

**The product owns its capabilities.** The engine starts with an EMPTY catalog and never registers anything on your
behalf. In your engine seam, once, at startup, before any ingest or query: register your own pack
(`load_pack("<your pack module>")`) and the engine capabilities you use
(`for m in engine_capabilities(): register_capability(m)`); `load_reference_pack()` only if you build on the engine's
contract/compliance worked example. The engine's `src/rag_wright/packs/reference_seam.py` shows the shape (it
registers in its constructor). Without it `ainvoke_subgraph` raises `KeyError` and the decision-model paths fall back
silently to the LLM.

The catalog is **one per process** (a known limitation): it is shared by every workspace, and registering a slug
replaces it for all of them. Workspaces can differ in store, corpus and configuration, not in which implementation a
slug resolves to. Tenants that need different implementations of one slug run in separate processes, or use one slug
per implementation chosen per tenant in your code.

Repository paths in this skill (`docs/`, `eval/`, `scripts/`, `tests/`, `src/`) are in the engine repository: read them there or on GitHub, at the tag matching your installed engine.

## 2. Ground before you write (graphify)

Never call an engine API you have not confirmed against the index. Build the grounding lanes and query them first:

- **`engine` lane** — index the INSTALLED `rag_wright` package (its site-packages location, or the engine repo if a
  path dep). This is the source of truth for what the API and the pack SDK expose and each symbol's exact
  signature. Ground every `rag_wright.api` / `rag_wright.pack_sdk` call against it before writing.
- **`engine-docs` lane** — index the engine repo's `docs/` (concepts, architecture, the domain-adaptation guide, the
  generated `docs/api/` references, the reference pack), checked out at the tag matching the installed engine
  version (the docs are not in the wheel; the engine's skills are, see "Engine skills to use"). This is how the engine's docs are grounded: query it
  to understand how a feature is meant to be wired, then confirm the call against the `engine` lane.
- **`project` lane** — your own product repo, as you build it.

**Rebuild both engine lanes whenever the engine changes**, before grounding the next call: after a Dependabot merge,
after `uv sync` picks up a new engine version, and (editable path dependency) after engine edits you pull in. A stale
lane grounds calls against the old API.

Discipline (same as elsewhere): the **code graph is the authority** for what exists and its signature; the engine's
**doc set + the docs MCP** are for understanding concepts and how a feature is meant to be wired. Order on any
unfamiliar surface: docs to understand → code graph to confirm → write the call. A method name seen in prose is not
proof the installed version has it.

## 3. Read the engine docs

The engine's `docs/` are NOT shipped in the wheel — read them in the engine repo (a path dep → `../RAG_Wright/docs/`)
or on GitHub. Start with **Concepts** and **Architecture**, then the **domain-adaptation guide**
(`docs/domain-adaptation/README.md`) — the 9-step sequence this skill operationalizes — and its companions
(ontology authoring, KG construction, entity resolution, authoring capabilities, classification & decision models),
plus **Quickstart**, **Reference pack**, and the generated **API reference** (`docs/api/`).

## 4. Follow the build sequence

Work the domain-adaptation guide, grounding each engine call (step 2) and writing each capability's eval first:

1. **Configure** — construct `EngineConfig` (+ your domain `.ttl` via `pack=`) and `open_workspace(config, corpus=…)`.
   With `pack=None` the workspace gets only the neutral schema (`Chunk`, `Entity`, `Relationship`, `Mentions`,
   `Span`, `Document`, `EmbeddedIn`, `AttachedTo`); your pack's types exist only if you pass `pack=` (the path to your
   `.ttl`; `open_workspace` then creates the types it declares).
2. **Author the `.ttl` pack** — your closed value sets, KG schema (`eng:` vocabulary), SHACL constraints, SKOS
   synonyms. Knowledge in the `.ttl`, never in Python (ADR-0066). → `ontology-authoring.md`.
3. **Build + register capabilities** — compose the engine's generic primitives by import; register YOUR domain
   graphs/models/skills via `register_capability(manifest)` with an `impl_ref`. → `authoring-capabilities.md`.
4. **Write each capability's eval first (TDD)** → the `creating-evals` skill; A/B rule vs classifier vs decision
   model vs LLM on it → `classifier-opportunity-analysis`, `setfit`, `laya`.
5. **Ingest the corpus** → the populated KG. `pipeline = build_ingestion(your_extractor, ...your hooks,
   tuning=IngestionTuning(...))`, then `await pipeline.aingest(ws, sources, cache_dir=...)`. Tune the hooks with
   `evaluate_ingestion` on your own sample documents before a full run. Embedded files and PDF attachments are
   ingested as child documents; a database-style table (spreadsheet, CSV or PDF) gives one unit per record
   row while forms stay whole (decided per table; override per source with `IngestSource.table_mode`), and
   `table_rows` returns the exact cells. See the `building-an-ingestion-capability` skill, ADR-0124 and
   `docs/api/`. **Entity resolution**: run your entity graph and resolver in
   `build_ingestion(document_hook=...)`, which runs once per document after its records are written; a
   first-class resolver config is still engine gap G1. → `kg-construction.md`, `entity-resolution.md`.
6. **Build the product seam** — your thin layer over the engine API + registered caps (tenancy, orchestration,
   the product tool surface). Wrap `open_workspace`/the invokers/`kg_read`/`measure_usage`.

**Using the decision model (Jev).** Register `jev_decision` (from `engine_capabilities()`) and set
`OPENROUTER_API_KEY` (`RAG_DECISION_MODEL` overrides the default profile `jev-1.13`). Without both, the reference
pack's decision paths silently fall back (to deterministic rules or the LLM); your own
`ainvoke_model("jev_decision", ...)` raises `KeyError` when it is not registered and `RuntimeError` when the key is
unset. Check both before trusting a run's numbers.

Read the engine's **reference pack** (`load_reference_pack()`) as a worked template — but the contract/compliance
domain is only an example; your pack and capabilities are your own.

## 5. Keep the engine current

The engine releases in batches (release-please; the engine's `docs/releasing.md`), so you never chase every
engine PR:

- Depend on a floor (`rag-wright>=X.Y.Z`, never `==`); `uv.lock` holds the exact version. Dependabot (uv ecosystem)
  opens a PR per new release; your CI — with a LIVE engine-seam test — gates it.
- Found an engine bug? Fix it **in the engine repo** (PR titled `fix(scope): …` so it lands in the next release),
  not by patching around it in the product.
- Need that fix before the next release? Temporarily pin
  `[tool.uv.sources] rag-wright = { git = "https://github.com/fzaidi-dai/RAG_Wright", rev = "<merged-sha>" }` and
  `uv lock`; drop the override when the release containing it arrives.

## Engine skills to use

`creating-evals` (eval-first), `building-an-ingestion-capability` (your ingestion hooks),
`classifier-opportunity-analysis` → `setfit`/`laya` (build a decision),
`authoring-a-capability` (register a cap), `qwen-vllm-modal` (bulk teacher-labeling substrate, with its Modal
deploy script). They ship inside the installed engine, at `rag_wright/.agents/skills/<name>/` (the convention
docling and fastapi use), so they always match your engine version. Link them into your repo's `.claude/skills/`
(gitignored) from the installed package, which works for a PyPI install and an editable path dependency alike:

```bash
SKILLS=$(uv run python -c "import pathlib, rag_wright; print(pathlib.Path(rag_wright.__file__).parent / '.agents' / 'skills')")
mkdir -p .claude/skills && for d in "$SKILLS"/*/; do ln -sfn "${d%/}" ".claude/skills/$(basename "$d")"; done
```

Re-run it after every engine upgrade (a symlink follows the installed version, but a newly added skill needs a new
link). The Addy-Osmani spec-driven/TDD/planning skills come from their own source, as the product template says.

## Anti-patterns

- Calling an engine API without grounding it against the `engine` lane first.
- Importing engine internals instead of `rag_wright.api` (and `rag_wright.pack_sdk` in pack code); forking or
  editing the engine.
- Treating the contract/compliance reference pack as the engine's purpose, or copying its vocabulary into your
  domain instead of authoring your own `.ttl`.
- Hand-constructing a capability's logic instead of invoking it by name through the invoker.
- Skipping the eval: write it before the implementation.
