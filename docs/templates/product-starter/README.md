# Product starter

Templates for a **new product repo** built on the RAG_Wright engine. Copy them into your product repo, fill the
placeholders, and run the Phase-0 setup — you get a `CLAUDE.md` and a build playbook that already carry the
engine/product boundary, the grounding discipline, the engine-usage skill pointers, and the spec-driven working
loop.

## Files

- `CLAUDE.md.template` → your repo's `CLAUDE.md` (the working rules Claude Code reads every session).
- `playbook.md.template` → your repo's `docs/playbook.md` (the build recipe + Phase-0 setup).
- `dependabot.yml` → your repo's `.github/dependabot.yml` (adopts each new engine release as a CI-gated PR, once the
  product depends on the published engine; playbook section 5).

## How to use

1. Copy both templates into the new repo (`CLAUDE.md.template` → `CLAUDE.md`, `playbook.md.template` →
   `docs/playbook.md`).
2. **Fill every `{{PLACEHOLDER}}`** (table below), then **delete the "FILL THESE FIRST" block** from each.
3. Run the playbook's **Section 2 (one-time Phase-0 setup)**: choose the engine dependency mode (playbook section 5:
   local co-development or released product), depend on the engine, register the engine capabilities at startup,
   build the grounding lanes, link the engine-authored skills (shipped in the installed engine) and the shared skills into `.claude/skills/`, and (released-product
   mode) copy `dependabot.yml` to `.github/dependabot.yml` so engine releases arrive as automated PRs.
4. Load the **`using-the-rag-wright-engine`** skill and follow the engine's domain-adaptation guide.

Confirm nothing is left unfilled: `rg '\{\{' CLAUDE.md docs/playbook.md` should return nothing.

## Placeholders

| Placeholder | Fill with |
|---|---|
| `{{PRODUCT_NAME}}` | your product's name |
| `{{PRODUCT_DOMAIN}}` | one line — what the product does |
| `{{ENGINE_DEP}}` | how you depend on the engine, per the mode in playbook section 5: local co-development = `rag-wright>={{ENGINE_FLOOR}}` + an editable `[tool.uv.sources]` path; released product = `uv add 'rag-wright>={{ENGINE_FLOOR}}'` from PyPI |
| `{{ENGINE_PATH}}` | where the engine repo/package is (e.g. `../RAG_Wright`) — for reading its docs/source |
| `{{ENGINE_FLOOR}}` | the minimum engine version you depend on (e.g. `0.8.0`): a `>=` floor, never `==`; it moves only for a released symbol |
| `{{ENGINE_LANE_PATH}}` / `{{ENGINE_DOCS_LANE_PATH}}` / `{{PROJECT_LANE_PATH}}` | your `graphify-out/` lane paths (engine = the installed `rag_wright` package; engine-docs = the engine repo's `docs/` at the installed version's tag + the engine skills linked in `.claude/skills/`; project = this repo) |
| `{{STACK}}` | your backend / frontend / model / infra choices |
| `{{SPEC}}` / `{{PLAN}}` / `{{TASKS}}` | your spec, plan, and task-ledger filenames |
| `{{REQ_SCHEME}}` / `{{AC_SCHEME}}` | your requirement / acceptance id schemes (e.g. `PR-N` / `AC-N`) |
| `{{PROJECT_FRONTEND_SKILL}}` | your project-scoped UI skill name, or `none` |
| `{{ARCADEDB_CONTAINER}}` | your product's ArcadeDB container name (e.g. `arcadedb-<your-product>`) |
| `{{ARCADEDB_HTTP_PORT}}` / `{{ARCADEDB_BINARY_PORT}}` | the host ports your instance publishes: a pair no other instance on the machine uses (the engine's test instance uses 2480/2424) |
| `{{PRODUCT_DEV_SKILL_SOURCES}}` | the folders of product-building skills you link from other repos (e.g. another product's generic UI, backend and tenancy skills), or `none`; linked into this repo's `.claude/skills/`, never globally (ADR-0134) |

## What the templates assume

- **Product → Engine, one way.** You consume the engine through `rag_wright.api` (and your pack's code through
  `rag_wright.pack_sdk`, which carries the entity-resolution building blocks); you never fork it or touch the store
  directly. A need neither covers is an engine request, written in your repo's `docs/engine-requests/`. The
  contract/compliance **reference pack** is the engine's worked example — a template, never your domain.
- **Your product owns its store.** It runs its own ArcadeDB instance and owns every database in it; the engine
  only mediates access.
- **The agent harness substrate is the engine's** (engine ADR-0139). Your product writes its agents, tools,
  prompts and guardrails on it; until the installed engine ships it (engine PS-32), keep agents thin and order the
  plan so harness-dependent work comes after it.
- The engine's documentation lives in the engine repo (not the wheel): concepts, architecture, installation,
  configuration, quickstart, the domain-adaptation guide, reference pack, and the generated API reference. Read it
  there or on GitHub.
