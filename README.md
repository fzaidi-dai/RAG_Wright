# RAG_Wright

**A domain-retargetable, open-core engine for hybrid retrieval + knowledge-graph RAG.**

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

RAG_Wright parses documents, chunks and embeds them, runs hybrid (dense + sparse) search with reranking, extracts a
knowledge graph with resolved entities, and answers **cited, abstention-willing** questions over a corpus — with
provenance and confidence on every claim. What a document *means* (the vocabulary, schema, and constraints) lives in
an ontology (`.ttl`), not in code, so standing up a **new domain is a pack, not a fork**.

## Why it exists

Building a retrieval + knowledge-graph system for a new domain usually means rebuilding the same plumbing every time,
or hardcoding one domain so deeply that the next one is a rewrite. RAG_Wright splits the two:

- the **engine** (this repo) is the reusable, domain-neutral machinery — ingestion and query pipelines, a capability
  runtime, one store, and the model seam;
- the **domain** is just an ontology pack + a few capabilities you author against the engine.

Answers carry citations and will abstain rather than guess, because retrieval over real corpora has to be
trustworthy. It is **open-core**: a user-facing product is a separate repo that depends on the engine one way
(**Product → Engine, never the reverse**). A runnable **reference pack** (a contract/compliance worked example) ships
here so the engine is demoable out of the box — your product brings its own domain pack, not this one.

## What you get

- **One store.** ArcadeDB holds both the hybrid retrieval index and the knowledge graph — no cross-store join to
  keep consistent.
- **A capability runtime (ARD).** Parsing, chunking, embedding, hybrid search, reranking, graph extraction, entity
  resolution, reasoning/generation and more are registered capabilities invoked by name through one API — and
  **discoverable by task** (`discover`), so an agent can plan over them. The engine ships with an **empty
  catalog**; you register your domain's capabilities (or opt into the reference pack).
- **Generic, hook-based ingestion.** `build_ingestion(extractor, ...)` runs the engine's own pipeline (parse, chunk,
  segment, index, group into units, write) around your domain's extractor; every other step has a default you can
  override. It reads PDF, Office (Word, PowerPoint) and spreadsheets (including hidden sheets and row-per-record
  tables), ingests embedded files and PDF attachments as linked child documents, and comes with a structural
  evaluation (`evaluate_ingestion`) to tune it on your own samples.
- **A neutral default schema.** A new workspace gets only the engine's types (`Chunk`, `Entity`, `Span`, `Document`
  and their edges); a domain's types come from its pack.
- **Knowledge in the ontology.** Closed vocabularies, schema, SHACL constraints and mappings live in a `.ttl` pack;
  code holds mechanism only.
- **Model-neutral.** Model access is a profile seam — OpenRouter by default, self-hosted open models (vLLM)
  supported — never a hardcoded provider.
- **Typed, provenance-first, test-driven.** Ships with `py.typed`; every answer carries citations; capabilities are
  built eval-first (the evaluation harnesses are in `eval/`).

## Documentation — start here

The whole doc set is under [`docs/`](docs/). Pick your path:

- **New here?** Read [Concepts](docs/concepts.md) (the mental model), then run the [Quickstart](docs/quickstart.md).
- **Installing / configuring?** [Installation](docs/installation.md) → [Configuration](docs/configuration.md).
- **Building a product on the engine?** The [domain-adaptation guide](docs/domain-adaptation/) walks the whole
  sequence (author a `.ttl` pack → build & register capabilities → eval-first → ingest → entity resolution → the
  product seam); a coding agent should drive it with the `using-the-rag-wright-engine` skill.
- **Going deep?** [Architecture](docs/architecture.md), the generated [API reference](docs/api/), the
  [Reference pack](docs/reference-pack.md), and the [ADR index](docs/adr/README.md).
- **Releases?** See the [CHANGELOG](CHANGELOG.md) and [how releases work](docs/releasing.md) (batched via
  release-please; products adopt them through automated dependency PRs).

## Install

```sh
uv add rag-wright
```

This installs the latest PyPI release (0.1.0). The generic ingestion builder, the neutral default schema and the
move of the reference pack to `rag_wright.packs` landed after 0.1.0; until the next release, depend on the engine
from git or a local path to get them (see [`docs/installation.md`](docs/installation.md)). Code written against
0.1.0 migrates with the
[breaking-changes record](docs/specs/ingestion-hooks/ing8-breaking-changes.md).

Runtime prerequisites: **ArcadeDB** (the store — run it locally with Docker, see
[`docs/installation.md`](docs/installation.md)); **a model provider** (an OpenRouter key by default, or a self-hosted
endpoint); and, only for NER (named entity recognition), the optional extra `uv add 'rag-wright[ner]'` +
`uv run python -m spacy download en_core_web_sm`.

## Quickstart (shape)

The whole public surface is `rag_wright.api`. The full runnable walkthrough (ingest a document, then query it) is in
[`docs/quickstart.md`](docs/quickstart.md) / [`examples/quickstart.py`](examples/quickstart.py):

```python
import asyncio
from rag_wright.api import EngineConfig, StoreConfig, open_workspace, load_reference_pack, ainvoke_subgraph

load_reference_pack()  # opt in to the contract/compliance worked example (the engine ships an empty catalog)

config = EngineConfig(store=StoreConfig(host="localhost", port="2480", user="root", password="<arcadedb-password>"))
ws = open_workspace(config, corpus="demo")  # corpus = the backend DB name

# assumes ACME_MSA was already ingested (the ingest step is in docs/quickstart.md)
async def main():
    out = await ainvoke_subgraph(
        "intra_document_qa", {"contract_id": "ACME_MSA", "question": "What is the liability cap?"}, resources=ws)
    print(out["answer"].answer, out["answer"].citations)   # a grounded, cited answer (or an abstention)

asyncio.run(main())
```

## Status

Alpha. Releases are published to PyPI (0.1.0 is the latest); newer engine work is on `main` until the next batched
release. The bundled reference pack is a worked example, not the product; restrictively-licensed evaluation corpora (CUAD/ACORD) are not shipped.

## Development

Python with uv (never bare `python`/`pip`):

```sh
uv sync && uv run pytest && uv run ruff check .
```

## Layout

```
src/rag_wright/
  api/           the stable, domain-agnostic public surface (import everything from here)
  capabilities/  the capability catalog + ARD runtime (manifests, registry, the invoker)
  ingestion/     the generic ingestion builder (build_ingestion), default segmenter/grouper, table rows, eval
  contracts/     Pydantic contracts + the shared identifiers (chunk_id, entity_id) and the ingestion hook contracts
  subgraphs/     the generic LangGraph scaffolding, semantic chunking and graph extraction
  models/        the model-profile seam (OpenRouter default / self-hosted open models)
  ontology/      the generic pack-schema reader + entity-registry derivation
  pack_sdk/      the pack-author tier: what a domain pack imports beyond rag_wright.api
  store/         the single ArcadeDB store behind the query seam
  spans/         page_map (page and bounding-box positions of spans)
  corpus/        document parsing + embedded-file extraction
  skills/        authored SKILL.md content for the generic capabilities
  util/          shared, capability-agnostic utilities
  packs/         the reference pack: packs/contracts (contract domain) and packs/compliance (built on it),
                 each with its pack.py, ontology (.ttl), capabilities, graphs, skills and MCP servers
docs/            documentation (docs/archive/ holds superseded/historical material)
```

Licensed under [MIT](LICENSE). Working rules for coding agents are in [`CLAUDE.md`](CLAUDE.md).
