# RAG_Wright

The **capability half** of a Hybrid Retrieval-Augmented Generation (RAG) system. See
[`SPEC.md`](SPEC.md) (v0.1) for the single source of truth and
[`docs/playbook.md`](docs/playbook.md) for the build approach.

RAG_Wright builds the capabilities in the spec (parsing, chunking, embedding, hybrid search,
reranking, graph extraction, entity resolution, ontology derivation, reasoning and generation,
and the RLM skill) as ordinary, tested software, and registers each one under its FR-C name.
It does **not** build the ingestion and query graphs; those are compiled from the companion
Orchestration Spec by the GraphWright compiler in a later step.

## Environment

Python with uv, pytest, and Pydantic. Everything runs through uv.

```sh
uv sync                 # create the environment from pyproject.toml + uv.lock
uv run pytest           # run the tests
uv run ruff check .     # lint
```

Never use `pip` or a bare `python`; always `uv add` / `uv run`.

## Layout

```
src/rag_wright/
  contracts/     Pydantic contracts: ontology, extraction contracts, shared identifiers
  capabilities/  the FR-C capabilities, each built and registered under its FR-C name
  store/         the single ArcadeDB store behind the query-skill seam (FR-S.1, FR-S.5)
  models/        the model-profile seam (OpenRouter default / local open-model)
  ontology/      ontology and entity-registry derivation from the Data Catalog (FR-C.8)
  mcp/           the governed MCP skill surface (FR-S.5)
  skills/        authored skill content, including the RLM SKILL.md (FR-C.10)
eval/            the evaluation suite and golden set by archetype (SPEC.md §12)
tests/           pytest (contract and capability tests)
docs/adr/        Architecture Decision Records
```

Working rules for Claude Code are in [`CLAUDE.md`](CLAUDE.md).
