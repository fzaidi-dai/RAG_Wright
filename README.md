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
  resolution, reasoning/generation and more are registered capabilities invoked by name through one API. The engine
  ships with an **empty catalog**; you register your domain's capabilities (or opt into the reference pack).
- **Knowledge in the ontology.** Closed vocabularies, schema, SHACL constraints and mappings live in a `.ttl` pack;
  code holds mechanism only.
- **Model-neutral.** Model access is a profile seam — OpenRouter by default, self-hosted open models (vLLM)
  supported — never a hardcoded provider.
- **Typed, provenance-first, test-driven.** Ships with `py.typed`; every answer carries citations; every capability
  has an eval.

## Documentation — start here

The whole doc set is under [`docs/`](docs/). Pick your path:

- **New here?** Read [Concepts](docs/concepts.md) (the mental model), then run the [Quickstart](docs/quickstart.md).
- **Installing / configuring?** [Installation](docs/installation.md) → [Configuration](docs/configuration.md).
- **Building a product on the engine?** The [domain-adaptation guide](docs/domain-adaptation/) walks the whole
  sequence (author a `.ttl` pack → build & register capabilities → eval-first → ingest → entity resolution → the
  product seam); a coding agent should drive it with the `using-the-rag-wright-engine` skill.
- **Going deep?** [Architecture](docs/architecture.md), the generated [API reference](docs/api/), the
  [Reference pack](docs/reference-pack.md), and the [ADR index](docs/adr/README.md).

## Install

```sh
uv add rag-wright
```

Runtime prerequisites: **ArcadeDB** (the store — run it locally with Docker, see
[`docs/installation.md`](docs/installation.md)); **a model provider** (an OpenRouter key by default, or a self-hosted
endpoint); and, only for NER, the optional extra `uv pip install 'rag-wright[ner]'` + `uv run python -m spacy
download en_core_web_sm`.

## Quickstart (shape)

The whole public surface is `rag_wright.api`. The full runnable walkthrough (ingest a document, then query it) is in
[`docs/quickstart.md`](docs/quickstart.md) / [`examples/quickstart.py`](examples/quickstart.py):

```python
import asyncio
from rag_wright.api import EngineConfig, StoreConfig, open_workspace, load_reference_pack, ainvoke_subgraph

load_reference_pack()  # opt in to the contract/compliance worked example (the engine ships an empty catalog)

config = EngineConfig(store=StoreConfig(host="localhost", port="2480", user="root", password="<arcadedb-password>"))
ws = open_workspace(config, corpus="demo")  # corpus = the backend DB name

async def main():
    out = await ainvoke_subgraph(
        "intra_document_qa", {"contract_id": "ACME_MSA", "question": "What is the liability cap?"}, resources=ws)
    print(out["answer"].answer, out["answer"].citations)   # a grounded, cited answer (or an abstention)

asyncio.run(main())
```

## Status

Alpha, and not yet on PyPI — install from source or a git/path dependency for now. The bundled reference pack is a
worked example, not the product; restrictively-licensed evaluation corpora (CUAD/ACORD) are not shipped.

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
  subgraphs/     the composite LangGraph pipelines (domain graphs; e.g. the reference pack's)
  models/        the model-profile seam (OpenRouter default / self-hosted open models)
  ontology/      the .ttl packs + entity-registry derivation (knowledge lives here)
  store/         the single ArcadeDB store behind the query seam
  spans/         operative-span segmentation + the classifier fleet
  reference/     the reference-pack facades (the worked example)
  mcp/           MCP tool surfaces over registered capabilities
  skills/        authored capability SKILL.md content
docs/            documentation (docs/archive/ holds superseded/historical material)
```

Licensed under [MIT](LICENSE). Working rules for coding agents are in [`CLAUDE.md`](CLAUDE.md).
