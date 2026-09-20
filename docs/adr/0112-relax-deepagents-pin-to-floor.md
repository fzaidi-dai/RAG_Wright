# ADR-0112: relax the `deepagents` pin from `==0.6.12` to a `>=0.7.15` floor

**Status:** accepted · **Date:** 2026-09-20 · **Resolves:** engine issue 0047 · **Amends:** ADR-0015 (the original exact pin)

## Context

`pyproject.toml` carried `deepagents==0.6.12`, an exact pin, with ADR-0015's rationale ("dynamic sub-agents are beta, floating = silent breakage"). An `==` in a **library's** dependency list is not a local decision — it binds every downstream consumer. RuleWright (the product) declared `deepagents>=0.6.12`, asked uv to upgrade, and got a **silent no-op**: the engine's `==0.6.12` made the resolver conclude "already latest" while 0.7.15 was current. The product needed 0.7's composable subagent middleware (a caller-supplied `SubAgentMiddleware` merges by `.name` instead of erroring as a duplicate) and had to add a `[tool.uv] override-dependencies` stopgap to move its own env (issue 0047).

The engine's own exposure is small: two modules import `deepagents` — `capabilities/okf_navigate.py` and `skills/rlm/agent.py` — both via `create_deep_agent(...)` + `deepagents.middleware.subagents.SubAgent`. Neither uses `write_todos`/`TodoListMiddleware` or `BASE_AGENT_PROMPT` (the two surfaces 0.7 changed most).

## Decision

**Relax the pin to a floor: `deepagents>=0.7.15`** (was `==0.6.12`), and upgrade the environment to 0.7.15.

- The `==` bound consumers to the engine's upgrade schedule; a floor lets a consumer move while still declaring the minimum the engine validated. Per the user's instruction, an open `>=` floor (no upper ceiling) was chosen. Issue 0047 suggested `>=0.7.15,<0.8` (a beta ceiling to stop a surprise major); that remains a one-line add if a ceiling is later wanted. The engine's own guard against breakage is its **behavioral tests**, not the pin — issue 0047's key observation is that the `==` did not prevent the one real breakage they hit (0.7 dropping the auto-todo middleware); a test would have.
- 0.7.15 validated against the engine: both call sites use `create_deep_agent(model=, tools=, system_prompt=, subagents=, middleware=, skills=)` — all valid kwargs in the 0.7.15 signature; `create_deep_agent` and `SubAgent` import unchanged; both modules import cleanly. **Full suite: 1584 passed, 44 skipped, 0 failures**, including the langchain cascade the bump pulled (langchain 1.3.13→1.4.2, langchain-core 1.4.9→1.6.3, langgraph, langsmith, langchain-anthropic, langchain-google-genai).
- Dropped packages (scikit-image, tifffile, soundfile) are optional-extra deps (scikit-image is under docling's `ocr-bench` extra; soundfile an audio extra), not core ingestion — the engine imports none directly, docling core imports fine. Not a regression; `uv sync --extra ocr-bench` restores them if a benchmark needs them.

## Consequences

- Downstream consumers (RuleWright) can now resolve `deepagents>=0.7.15` against the engine and **delete their `override-dependencies` stopgap**.
- **Open follow-up for ADR-0015 (not done here).** ADR-0015 Q2 (design B′: interpreter-driven RLM recursion) rests on a premise stated for `deepagents==0.6.12` — that a self-referential sub-agent is *not constructible* because `SubAgentMiddleware.__init__` compiles its roster eagerly. 0.7 is exactly the release that reworked subagent-middleware composition, so that premise should be re-validated on 0.7.15: design B′ still works regardless (the interpreter drives recursion), but the *direct* self-dispatch form it rejected may now be constructible, which would give ADR-0015 Q2 a cheaper option. Flagged, not tested — a separate RLM task.
- `langchain-quickjs==0.3.2` remains an exact pin (interpreter runtime is beta, ADR-0015); out of scope here (issue 0047 named it too — relax separately if/when validated).
- A deeper live RLM/okf agent run on 0.7.15 (beyond import + unit tests) is deferred to whichever task next exercises those skills end-to-end.

Test evidence: full suite 1584 passed / 44 skipped after the upgrade; deepagents 0.7.15 installed and imports validated.
