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
  write an engine request (section 6) — don't work around it here: a product-side patch hides the defect from
  every other product built on the engine.
- **Your product owns its store and its data; the engine mediates access.** Your product runs its own ArcadeDB and
  has no ArcadeDB client of its own (section 1).
- **The public surface is `rag_wright.api`; a pack also uses `rag_wright.pack_sdk`.** From `rag_wright.api` import
  `EngineConfig`/`StoreConfig` (`StoreConfig.from_env()` reads `ARCADEDB_*`)/`open_workspace`
  (`temporary_workspace`/`drop_workspace` for disposable and removed databases), the invokers
  (`ainvoke_subgraph`/`invoke_model`/`ainvoke_model`), discovery (`capability_index`/`discover`), the KG accessors
  (`kg_read` with `key_range`, `kg_write`, `kg_edges` with `NOT_NULL`, `kg_count`/`kg_delete`/`kg_update`,
  `entities_by_name`, `span_positions`), `pack_store(ws, cls)` (build your pack's store extension over the
  workspace's store; the store itself stays private), metering and tracing (`measure_usage`, `record_usage`,
  `traced_run`/`traced_step`), `agent_chat_model` (an agent's chat model from an engine model id), answers (`agenerate_answer`, `ajudge_spans` with `EvidenceItem`/`Condition`, the
  models from the workspace's roles, your domain wording via `guidance=`), `ModelRole`, pack loading
  (`register_capability`/`load_pack`/`engine_capabilities`/`load_reference_pack`), the document helpers
  (`parse_document`/`aparse_document`, `parse_document_bytes`/`aparse_document_bytes` for uploads,
  `source_document`/`table_rows`, `decode_bbox`, `document_of`), and the **ingestion surface**: `build_ingestion`
  (returns an `IngestionPipeline`; `await pipeline.aingest(ws, sources, cache_dir=...)` returns an
  `IngestionReport`), its hook contracts (`Segmenter`, `SpanTagger`, `UnitGrouper`, `BoundaryDecider`,
  `UnitRepresentative`, `BoundaryDiscoverer` with `default_chunk_discoverer(guidance=...)`, `Extractor`,
  `RecordWriter`, `DocumentHook`, checked by `check_tiling`/`check_units`/`check_extraction`), `IngestionTuning`,
  `IngestSource` (a `path`, or `data` + `name` for bytes; `table_mode`, `include_hidden_sheets`, your own `metadata`
  for the document's `Document` node) and
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
uv add 'rag-wright>=0.8.0'              # released-product mode (PyPI); see below for local co-development
```

Stand up the runtime prerequisites you provide: ArcadeDB (the store), a model provider (OpenRouter or self-hosted
vLLM), and — only for NER — `uv add 'rag-wright[ner]'` + `uv run python -m spacy download en_core_web_sm`.
Read the engine's **installation** + **configuration** docs for the exact `.env` and `EngineConfig` fields.

**ArcadeDB is your product's, all of it.** Run your own instance (installation, "Run your product's ArcadeDB"): its
own container name, its own host ports (a pair no other instance on the machine uses; the engine's test instance
uses 2480/2424), its data directory in your repo (gitignored) or on storage you manage. Make your settings default
to your own port, so an unset variable cannot reach another instance and appear to work. Your product code has no
ArcadeDB client: every read and write goes through `rag_wright.api`.

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

**Workspaces.** One corpus and one `EngineConfig` per tenant, a `cache_dir` per tenant, document metadata on
`IngestSource(metadata=...)`; models follow the workspace's `EngineConfig.models` in every call that takes it
(`use_workspace_models(ws)` for a call that does not). To change a tenant's config, open its corpus again with the
new config: a different config replaces the cached handle, no restart needed. `docs/workspaces.md` lists what applies per workspace, per call and per process.

**Databases.** `open_workspace(config, corpus=...)` creates a missing database by default (and logs
`created database '<name>'`); where the database must already exist (a read path, a query service, an explicit
override), pass `create=False`, which raises `LookupError` naming the databases that do exist instead of opening an
empty graph. A probe, measurement or test that needs its own database uses
`with temporary_workspace(config, prefix="probe") as ws:`; the database is dropped when the block exits, also when it
raises. Remove a database with `drop_workspace(config, corpus=...)` (it refuses a blank name and cannot be undone);
never delete its files or call the ArcadeDB server yourself.

**Hermetic tests stub the door the code opens.** Your code reaches the store through `open_workspace`; a fast test
stubs that. A stub on an entry point the code no longer calls lets the test reach a real store and create a database
on every run, without failing.

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
plus **Quickstart**, **Workspaces** (`docs/workspaces.md`: per workspace, per call, per process), **Reference
pack**, and the generated **API reference** (`docs/api/`).

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
   the product tool surface). Wrap `open_workspace`/the invokers/`kg_read`/`measure_usage`. Classify each operation
   before building it: deterministic (the backend calls a pre-wired workflow), agent-fronted (a fixed workflow
   reached through an agent), or composed by a model at run time; build each workflow once, as a tool that both the
   UI and an agent call, and never put a model in the path of an unambiguous button click.

**PDFs.** Call the parse and ingest functions concurrently as you like: the engine parses one PDF at a time inside
the process (a crash in the PDF library otherwise, engine ADR-0138) and keeps everything after the parse concurrent.
Count an upload's pages with `pdf_page_count(data)`, which reads under the same lock; product code never imports the
PDF library itself.

**Agents.** Build a product agent (Deep Agents or LangGraph) on the model the engine gives it, by engine model id:
`create_deep_agent(model=agent_chat_model("<model id>", max_retries=0), tools=[...])` (`None` takes the `GENERAL`
role's model). The profile decides endpoint, key and the agent turn's reasoning flag, so your code names no model,
vendor or vendor flag; every call, a subagent's included, is recorded into `measure_usage()` with the provider's
real cost and traced when tracing is on. The generic harness substrate (the loop, error levels and deadlines,
retries, interrupts and human-in-the-loop, replay on resume, memory practice, metering) is engine scope (engine
ADR-0139, PS-32): it arrives as a third public tier with the `building-an-agent-harness` skill. Until your installed
engine has it, keep agents thin and never write a substrate of your own; your product keeps its agents, tools,
prompts, skills, guardrails and decision models.

**Using a self-hosted model (Qwen on Modal).** Deploy it from the engine and use its model id like any model:
`server = await adeploy_model_server(ModelServerSpec(name="<app>", max_num_seqs=<concurrency target>))` (the locked
setup of the `qwen-vllm-modal` skill; needs `rag-wright[modal]`, a Modal login and `VLLM_API_KEY` in `.env`), then
`EngineConfig(models={"general": server.model_id, ...})`. Other processes reconnect with `amodel_server(spec)`;
call `await_model_server(server, timeout_s=<startup budget>)` before traffic (cold start is minutes; the default
waits 25 min); `stop_model_server(name)` stops billing. `amodel_server_status(spec)` looks without waking it (deployed,
running, healthy, weights cached). There is no default `VLLM_API_KEY`. Count concurrency end to end: `aingest` runs
`min(document_concurrency, documents) x extract_concurrency` calls per run, parallel runs add up, and the sum must
stay below `max_num_seqs` (`aingest` warns when its own peak exceeds it).
Full walk-through: `docs/configuration.md`, "A self-hosted model server".

**Using the decision model (Jev).** Register `jev_decision` (from `engine_capabilities()`) and set
`OPENROUTER_API_KEY` (`RAG_DECISION_MODEL` overrides the default profile `jev-1.13`). Without both, the reference
pack's decision paths silently fall back (to deterministic rules or the LLM); your own
`ainvoke_model("jev_decision", ...)` raises `KeyError` when it is not registered and `RuntimeError` when the key is
unset. Check both before trusting a run's numbers.

Read the engine's **reference pack** (`load_reference_pack()`) as a worked template — but the contract/compliance
domain is only an example; your pack and capabilities are your own.

## 5. Evals behind your product's backend
Domain experts own the gold; your UI lets them upload it, run it and compare runs. The engine gives you the pieces
(the `creating-evals` skill has the method and a complete example):
- **Upload a gold set**: `aload_gold_set(IngestSource(data=uploaded_bytes, name=filename), GoldMapping(...),
  name=..., version=...)` -- CSV / XLSX / JSONL as the expert wrote it; a `GoldImportError` says where the file is
  wrong, which you show as is. Client data stays `local_only` (the default).
- **Run it as a background job**, never inside the request: start `arun_eval(gold, task, scorers, repeats=...,
  progress=...)` as a task you keep a reference to, return a job id at once, and let the UI poll. `progress` receives
  `[eval] i/N <item> r<k> ok|error` lines; `evaluate_ingestion`'s async form `aevaluate_ingestion` works the same
  way for ingestion structure. The synchronous `run_eval` raises inside a running event loop.
- **Return the `EvalReport`** (`report.model_dump(mode="json")`): `passed`, each scorer's gate against its
  thresholds and its diagnostics, errors and flips kept apart, every item's outputs.
- **Keep run history in your store**: save each report with its gold version; `rescore_eval` scores a saved report
  again (new thresholds, a corrected gold) without calling anything. With `langfuse=True` the runs also appear in
  Langfuse with their links (`report.run_urls`), for teams that use it.
- **The task is your capability through the capability layer**, so the eval measures what production runs.

## 6. Keep the engine current

The engine releases in batches (release-please; the engine's `docs/releasing.md`), so you never chase every
engine PR:

- Depend on a floor (`rag-wright>=X.Y.Z`, never `==`); `uv.lock` holds the exact version. Dependabot (uv ecosystem)
  opens a PR per new release; your CI — with a LIVE engine-seam test — gates it.
- Found an engine bug, or need something the engine does not have? Write an **engine request** in your repo,
  `docs/engine-requests/YYYY-MM-DD-<slug>.md`: what you found, the evidence (a reproduction, a measurement, the
  exact error), what you want, and what you do meanwhile. The engine fixes it in its own repo (a `fix(scope): …`
  commit, so the next release carries it); never patch around it in the product. Read the engine's
  `docs/domain-adaptation/_engine-gaps.md` first: the gap may be known.
- An engine symbol on the engine's main branch but not yet released is reached through the editable path
  (local co-development), never through an internal import; move your floor only for a released symbol.
- Keep a committed snapshot of the installed `rag_wright.api.__all__` and a test that compares them: a change to the
  surface you build on is then named the day it arrives, and any other engine change needs nothing from you.
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
link). Link these, and any product-building skills another repo writes for product developers, into this repo's
`.claude/skills/` only, never into the global Claude Code skills, where they would apply in every repo on the machine. The Addy-Osmani spec-driven/TDD/planning skills come from their own source, as the product template says.

## Anti-patterns

- Calling an engine API without grounding it against the `engine` lane first.
- Importing engine internals instead of `rag_wright.api` (and `rag_wright.pack_sdk` in pack code); forking or
  editing the engine.
- Treating the contract/compliance reference pack as the engine's purpose, or copying its vocabulary into your
  domain instead of authoring your own `.ttl`.
- Hand-constructing a capability's logic instead of invoking it by name through the invoker.
- Skipping the eval: write it before the implementation.
- Patching around an engine defect in product code instead of writing the engine request.
- Giving product code an ArcadeDB client, pointing it at another product's or the engine's instance, or deleting a
  database's files by hand (`drop_workspace` removes one; `temporary_workspace` keeps scratch from piling up).
- Naming a model, vendor or vendor flag in product code; writing your own agent harness substrate.
