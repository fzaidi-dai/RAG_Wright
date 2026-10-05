# Product starter

Templates for a **new product repo** built on the RAG_Wright engine. Copy them into your product repo, fill the
placeholders, and run the Phase-0 setup — you get a `CLAUDE.md` and a build playbook that already carry the
engine/product boundary, the grounding discipline, the engine-usage skill pointers, and the spec-driven working
loop.

## Files

- `CLAUDE.md.template` → your repo's `CLAUDE.md` (the working rules Claude Code reads every session).
- `playbook.md.template` → your repo's `docs/playbook.md` (the build recipe + Phase-0 setup).

## How to use

1. Copy both templates into the new repo (`CLAUDE.md.template` → `CLAUDE.md`, `playbook.md.template` →
   `docs/playbook.md`).
2. **Fill every `{{PLACEHOLDER}}`** (table below), then **delete the "FILL THESE FIRST" block** from each.
3. Run the playbook's **Section 2 (one-time Phase-0 setup)**: depend on the engine, build the grounding lanes, and
   link the engine-authored + shared skills into `.claude/skills/`.
4. Load the **`using-the-rag-wright-engine`** skill and follow the engine's domain-adaptation guide.

Confirm nothing is left unfilled: `rg '\{\{' CLAUDE.md docs/playbook.md` should return nothing.

## Placeholders

| Placeholder | Fill with |
|---|---|
| `{{PRODUCT_NAME}}` | your product's name |
| `{{PRODUCT_DOMAIN}}` | one line — what the product does |
| `{{ENGINE_DEP}}` | how you depend on the engine (`uv add rag-wright`, a path dep, or git) |
| `{{ENGINE_PATH}}` | where the engine repo/package is (e.g. `../RAG_Wright`) — for reading its docs/source |
| `{{ENGINE_LANE_PATH}}` / `{{PROJECT_LANE_PATH}}` | your `graphify-out/` lane paths (engine = the installed `rag_wright` package; project = this repo) |
| `{{STACK}}` | your backend / frontend / model / infra choices |
| `{{SPEC}}` / `{{PLAN}}` / `{{TASKS}}` | your spec, plan, and task-ledger filenames |
| `{{REQ_SCHEME}}` / `{{AC_SCHEME}}` | your requirement / acceptance id schemes (e.g. `PR-N` / `AC-N`) |
| `{{PROJECT_FRONTEND_SKILL}}` | your project-scoped UI skill name, or `none` |

## What the templates assume

- **Product → Engine, one way.** You consume the engine through `rag_wright.api`; you never fork it or touch the
  store directly. The contract/compliance **reference pack** is the engine's worked example — a template, never
  your domain.
- The engine's documentation lives in the engine repo (not the wheel): concepts, architecture, installation,
  configuration, quickstart, the domain-adaptation guide, reference pack, and the generated API reference. Read it
  there or on GitHub.
