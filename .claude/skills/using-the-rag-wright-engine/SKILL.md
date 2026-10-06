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

- **Product → Engine, one way.** Never fork, edit, or reach into engine internals. If the engine needs a change,
  flag it upstream — don't work around it here.
- **The public surface is `rag_wright.api`.** Import `EngineConfig`/`StoreConfig`/`open_workspace`, the invokers
  (`ainvoke_subgraph`/`invoke_model`/`ainvoke_model`), discovery (`capability_index`/`discover`),
  `kg_read`/`kg_write`/`kg_edges`/`entities_by_name`, `measure_usage`, and
  `register_capability`/`load_reference_pack`/`reference_pack` — nothing deeper (never `ArcadeDBStore`,
  `query_embedder`, or engine id formats). To **plan** over the engine for a task, `discover(task, resources=ws)`
  returns the best-matching capabilities (embedding-ranked); then invoke the top ones by slug.

## 1. Install

```sh
uv add rag-wright                       # or a path/git dep pre-publish (see the engine's installation doc)
```

Stand up the runtime prerequisites you provide: ArcadeDB (the store), a model provider (OpenRouter or self-hosted
vLLM), and — only for NER — `uv pip install 'rag-wright[ner]'` + `uv run python -m spacy download en_core_web_sm`.
Read the engine's **installation** + **configuration** docs for the exact `.env` and `EngineConfig` fields.

## 2. Ground before you write (graphify)

Never call an engine API you have not confirmed against the index. Build the grounding lanes and query them first:

- **`engine` lane** — index the INSTALLED `rag_wright` package (its site-packages location, or the engine repo if a
  path dep). This is the source of truth for what the API exposes and each symbol's exact signature. Ground every
  `rag_wright.api` call against it before writing.
- **`project` lane** — your own product repo, as you build it.

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
2. **Author the `.ttl` pack** — your closed value sets, KG schema (`eng:` vocabulary), SHACL constraints, SKOS
   synonyms. Knowledge in the `.ttl`, never in Python (ADR-0066). → `ontology-authoring.md`.
3. **Build + register capabilities** — compose the engine's generic primitives by import; register YOUR domain
   graphs/models/skills via `register_capability(manifest)` with an `impl_ref`. → `authoring-capabilities.md`.
4. **Write each capability's eval first (TDD)** → the `creating-evals` skill; A/B rule vs classifier vs decision
   model vs LLM on it → `classifier-opportunity-analysis`, `setfit`, `laya`.
5. **Ingest the corpus** → the populated KG; **entity resolution** wires your resolver in your ingestion graph
   (no public seam yet — see the engine-gaps register). → `kg-construction.md`, `entity-resolution.md`.
6. **Build the product seam** — your thin layer over the engine API + registered caps (tenancy, orchestration,
   the product tool surface). Wrap `open_workspace`/the invokers/`kg_read`/`measure_usage`.

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

`creating-evals` (eval-first), `classifier-opportunity-analysis` → `setfit`/`laya` (build a decision),
`authoring-a-capability` (register a cap), `qwen-vllm-modal` (bulk teacher-labeling substrate). Pull these (and the
Addy-Osmani spec-driven/TDD/planning skills) into your repo via your setup step, as the product template does.

## Anti-patterns

- Calling an engine API without grounding it against the `engine` lane first.
- Importing engine internals instead of `rag_wright.api`; forking or editing the engine.
- Treating the contract/compliance reference pack as the engine's purpose, or copying its vocabulary into your
  domain instead of authoring your own `.ttl`.
- Hand-constructing a capability's logic instead of invoking it by name through the invoker.
- Skipping the eval: write it before the implementation.
