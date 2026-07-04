# ADR-0001: Stack and core library choices

- Status: Accepted
- Date: 2026-07-04
- Deciders: farhan.zaidi@dreamai.io, Claude Code
- Phase: 0 (setup)

## Context

Phase 0 declares the environment and the core libraries the capabilities build on. Several
slots in the spec and CLAUDE.md admit more than one concrete choice ("an OpenAI-compatible
client or the LangChain OpenRouter chat model"; "the MCP server framework"; "the ArcadeDB
Python client"). These are load-bearing: future sessions must not silently diverge, and
changing the model client or the store driver is an ask-first change per CLAUDE.md boundaries.
This ADR records the choices made so they are honored on resume.

## Decision

- **Language / environment:** Python 3.12 via uv (`pyproject.toml` + `uv.lock`, one `.venv`).
  Everything runs through `uv run` / `uv add`. (Playbook default.)
- **Model client (OpenRouter / model-profile seam):** `langchain-openai` (`ChatOpenAI`), not
  the raw `openai` SDK. Rationale: consistency with the companion GraphWright / orchestration
  project. `openai` remains only a transitive dependency.
- **MCP server framework:** the official `mcp` SDK (bundles FastMCP), not standalone `fastmcp`.
- **ArcadeDB driver:** `arcadedb-python` 0.4.0 (github.com/stevereiner/arcadedb-python) —
  multi-model with vector support; HTTP by default, optional Postgres-wire via the
  `[postgresql]` extra.
- **Project layout:** a single installable package `src/rag_wright/` with component
  subpackages (`contracts`, `capabilities`, `store`, `models`, `ontology`, `mcp`, `skills`).
  This reinterprets the literal `src/contracts/` in CLAUDE.md/playbook for packaging
  correctness (avoids generic top-level import names colliding in the venv).
- **Framework Graphify graph scoping:** structural (AST) extraction only — free, no LLM —
  noise-pruned (drop generated type stubs `openai/types`, test trees, spaCy language tables)
  to keep the grounding index at ~12.5k nodes rather than whole-monorepo scale, per the
  playbook's "scoped to the modules actually used, not whole monorepos."
- **Deferred, not guessed:** an OpenIE tool for FR-C.6 lightweight extraction is added at its
  capability task; spaCy covers NER plus dependency parsing until then.

## Consequences

- `langchain-openai` aligns both halves of the system. Structured output goes through
  `ChatOpenAI.with_structured_output(...)` via the model-profile seam keyed by model id, never
  a hardcoded provider or model flag in node/agent code (CLAUDE.md standing rule).
- `openai` is present transitively but is not in the framework graph; `langchain_openai` is the
  grounding target for the model client.
- The ArcadeDB HTTP-first driver fits the single-store rule (FR-S.1); the `[postgresql]` extra
  is available if the wire protocol is preferred later.
- References to `src/contracts/` in CLAUDE.md/playbook map to `src/rag_wright/contracts/`.
- Reversing any of these later — especially the model client or the store driver — is an
  ask-first change (SPEC.md section 14; CLAUDE.md boundaries).
