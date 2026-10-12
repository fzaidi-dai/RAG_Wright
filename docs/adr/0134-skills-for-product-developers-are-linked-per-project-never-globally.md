# ADR-0134: Skills for product developers are linked into each product repo, never installed globally

**Status:** accepted · **Date:** 2026-10-12 · **Related:** PS-10 (the engine's skills ship in the wheel),
ADR-0052 (engine/product split)

## Context

Two kinds of repo now write skills for people building a product on the engine. The engine writes skills about using
the engine (`using-the-rag-wright-engine`, `creating-evals`, ...). A product may write generic product-building
skills (UI, backend, tenancy, workspaces, the agent harness) that a later product can reuse. A skill linked into
`~/.claude/skills` applies in every repo on the machine: an agent working on the engine, or on an unrelated project, then sees product-building advice that is not meant for it, and a globally linked skill
follows one checkout rather than the version a given product depends on. The product-starter templates said "link
the shared generic skills from their source" without saying where to link them.

## Decision

Skills live in one of three places, by who they are for:

1. **The developer's own way of working** (TDD, planning, debugging, ADRs) may be installed in `~/.claude/skills`.
   Nothing from the engine or a product repo is linked there.
2. **Skills for working on one repo only** are real folders in that repo's `.claude/skills/` and are never shipped.
3. **Skills for product developers** live in a source folder in the repo that writes them, and each product links them
   into its own `.claude/skills/` (gitignored), never globally:
   - the engine's: `src/rag_wright/.agents/skills/`, shipped in the wheel (PS-10). A product links them from its
     installed engine (the copy that matches the version it runs) or, while co-developing, from an engine checkout;
     both are links into the product's own `.claude/skills/`;
   - a product's own: wherever that product keeps them, outside the engine's concern.

The engine's docs, templates and skills that show how to link skills only ever link into the product repo's
`.claude/skills/`; `tests/arch/test_shipped_skills.py` fails on a command in them that links into
`~/.claude/skills`.

## Consequences

- An engine session sees the engine's skills through this repo's `.claude/skills/` links only; a session in an
  unrelated repo does not see them.
- A product re-runs its link step after an engine upgrade (as before) and after pulling a new generic skill.
- The engine governs only its own skills, docs and templates; where a product keeps its skills is the product's
  decision, and the engine's templates name the slot (`{{PRODUCT_DEV_SKILL_SOURCES}}`) without naming a folder.
