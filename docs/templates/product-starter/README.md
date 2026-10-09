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
| `{{ENGINE_FLOOR}}` | the minimum engine version you depend on (e.g. `0.3.0`): a `>=` floor, never `==` |
| `{{ENGINE_LANE_PATH}}` / `{{ENGINE_DOCS_LANE_PATH}}` / `{{PROJECT_LANE_PATH}}` | your `graphify-out/` lane paths (engine = the installed `rag_wright` package; engine-docs = the engine repo's `docs/` at the installed version's tag + the engine skills linked in `.claude/skills/`; project = this repo) |
| `{{STACK}}` | your backend / frontend / model / infra choices |
| `{{SPEC}}` / `{{PLAN}}` / `{{TASKS}}` | your spec, plan, and task-ledger filenames |
| `{{REQ_SCHEME}}` / `{{AC_SCHEME}}` | your requirement / acceptance id schemes (e.g. `PR-N` / `AC-N`) |
| `{{PROJECT_FRONTEND_SKILL}}` | your project-scoped UI skill name, or `none` |

## What the templates assume

- **Product → Engine, one way.** You consume the engine through `rag_wright.api`; you never fork it or touch the
  store directly. One set of helpers is not on `rag_wright.api` yet (the
  entity-resolution building blocks, their contracts and the entity registry); the templates list it as an engine
  gap to flag. The contract/compliance
  **reference pack** is the engine's worked example — a template, never your domain.
- The engine's documentation lives in the engine repo (not the wheel): concepts, architecture, installation,
  configuration, quickstart, the domain-adaptation guide, reference pack, and the generated API reference. Read it
  there or on GitHub.
