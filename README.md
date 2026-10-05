# RAG_Wright

A **domain-retargetable, open-core engine for hybrid retrieval + knowledge-graph RAG**. It parses documents,
chunks and embeds them, runs hybrid (dense + sparse) search with reranking, extracts a knowledge graph with entity
resolution, and answers cited, abstention-willing questions over a corpus — with provenance and confidence on every
claim. Domain knowledge lives in an ontology (`.ttl`), not in code, so a new domain is a pack, not a fork.

> **Engine, not the product (ADR-0052).** This repository is the reusable **engine**. A user-facing product (e.g. a
> contract-management + compliance app) is a separate repository that depends on it one way: **Product → Engine,
> never the reverse.** A runnable **reference pack** (a contract/compliance worked example) ships here so the
> open-core is demoable; your product brings its own domain pack.

## What it gives you

- **One store.** ArcadeDB holds both the hybrid retrieval index and the knowledge graph — no cross-store join to
  keep consistent.
- **A capability runtime (ARD).** Parsing, chunking, embedding, hybrid search, reranking, graph extraction, entity
  resolution, reasoning/generation and more are registered capabilities, invoked by name through one API. The
  engine ships with an **empty catalog**; you register your domain's capabilities (or opt into the reference pack).
- **Knowledge in the ontology.** Closed vocabularies, schema, constraints (SHACL) and mappings live in a `.ttl`
  pack; code holds mechanism only.
- **Model-neutral.** Model access is a profile seam — OpenRouter by default, self-hosted open models (vLLM) as a
  supported mode — never a hardcoded provider.
- **Typed, provenance-first, test-driven.** Shipped with `py.typed`; every answer carries citations; every
  capability has an eval.

## Install

```sh
uv add rag-wright
```

Runtime prerequisites:

- **ArcadeDB** (the single store) — run it locally with Docker; see [`docs/ArcadeDB_Local.md`](docs/ArcadeDB_Local.md).
- **A model provider** — an OpenRouter key by default, or a self-hosted open-model endpoint (the model-profile seam).
- **Optional spaCy NER** — `uv pip install 'rag-wright[ner]'` then `uv run python -m spacy download en_core_web_sm`
  (the model is a runtime download, not a packaged dependency; configurable via `RAG_SPACY_MODEL`).

## Quickstart (shape)

The whole public surface is `rag_wright.api`. A minimal "open a workspace and ask a question over the reference
pack" looks like this; the full, runnable walkthrough (including ingesting a document) is in
[`docs/quickstart.md`](docs/quickstart.md).

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

## Documentation

Current and being consolidated under [`docs/`](docs/) (engine-prep WS2):

- [Concepts](docs/concepts.md) · [Architecture](docs/architecture.md) — the mental model and the engine/product boundary
- [Installation](docs/installation.md) · [Configuration](docs/configuration.md) · [Quickstart](docs/quickstart.md)
- [API reference](docs/api/) — generated from `rag_wright.api`
- [Reference pack](docs/reference-pack.md) — the contract/compliance worked example
- [Building a new domain](docs/domain-adaptation/) — the domain-adaptation guide (ontology, KG, entity resolution, capabilities, evals)
- [Architecture Decision Records](docs/adr/README.md) — the grouped decision index
- [Engine-platform spec](docs/specs/engine-platform/SPEC.md) — the engine API + capability runtime boundary

## Development

Python with uv (never bare `python`/`pip`):

```sh
uv sync                 # create the environment from pyproject.toml + uv.lock
uv run pytest           # run the tests
uv run ruff check .     # lint
```

## Layout

```
src/rag_wright/
  api/           the stable, domain-agnostic public surface (import everything from here)
  capabilities/  the capability catalog + ARD runtime (manifests, registry, the invoker)
  subgraphs/     the composite LangGraph pipelines (ingestion, retrieval, QA, compliance)
  models/        the model-profile seam (OpenRouter default / self-hosted open models)
  ontology/      the .ttl packs + entity-registry derivation (knowledge lives here)
  store/         the single ArcadeDB store behind the query seam
  spans/         operative-span segmentation + the classifier fleet
  reference/     the reference-pack facades (the worked example)
  mcp/           MCP tool surfaces over registered capabilities
  skills/        authored capability SKILL.md content
eval/            the evaluation suite + golden sets by archetype
tests/           pytest (contract, capability, and architecture tests)
docs/            documentation; docs/archive/ holds superseded/historical material
```

Licensed under MIT. Working rules for coding agents are in [`CLAUDE.md`](CLAUDE.md).
