# RuleWright handoff: `deepagents` pin relaxed to a floor (issue 0047) — delete your override

Date: 2026-09-20 · on `origin/main` · ADR-0112 (amends ADR-0015) · **Dependency change. The engine no longer hard-pins `deepagents`; you can resolve `>=0.7.15` against it and drop your stopgap.**

---

## What changed

`pyproject.toml`: `deepagents==0.6.12` → **`deepagents>=0.7.15`** (a floor, not a hard pin). The engine is upgraded to 0.7.15 and validated on it (full suite 1584 passed / 44 skipped).

## What you can do now

**Delete the `[tool.uv] override-dependencies = ["deepagents>=0.7.15"]` stopgap** from the product `pyproject.toml`. The engine now declares a floor that permits 0.7.15, so a normal resolve moves you there without an override — your `uv lock --upgrade-package deepagents` will no longer silently no-op against the engine's constraint. The one-way boundary is intact (this is an engine-repo change; nothing product-side leaked in).

## Notes carried over from your issue

- The engine's two `deepagents` call sites (`capabilities/okf_navigate.py`, `skills/rlm/agent.py`) use `create_deep_agent(model=, tools=, system_prompt=, subagents=, middleware=, skills=)` — all valid on 0.7.15; neither uses `write_todos`/`TodoListMiddleware` or `BASE_AGENT_PROMPT`, so the two 0.7 hazards you flagged (todo middleware now opt-in; `BASE_AGENT_PROMPT` deprecated, removal in 0.9) don't touch engine code. They remain your concern product-side, guarded by behavioral tests (a pin didn't catch it for you — a test did).
- **No upper ceiling** was added (open `>=0.7.15`), per the request. Your issue suggested `>=0.7.15,<0.8` as a beta guard; that's a one-line add if the engine later wants it. If you rely on a ceiling downstream, set it in your own manifest.
- **ADR-0015 Q2 re-check (engine-owned, deferred):** the RLM design-B′ premise ("a self-referential sub-agent is not constructible on 0.6.12 because `SubAgentMiddleware` compiles its roster eagerly") sits in the exact area 0.7 reworked. Design B′ still works; whether the *direct* self-dispatch form is now constructible is flagged for a later engine RLM task, not resolved here. You correctly called this "may reopen a design decision, not just a version number" — noted, tracked in ADR-0112.
- `langchain-quickjs==0.3.2` is still an exact pin (interpreter runtime is beta) — out of scope here; raise separately if you need it relaxed.

Reference: ADR-0112, ADR-0015 (amended), engine issue `docs/engine-issues/0047-...`, `pyproject.toml`. Full suite: 1584 passed.
