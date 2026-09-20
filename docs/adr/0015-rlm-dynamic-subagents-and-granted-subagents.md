# ADR-0015: RLM is interpreter + dynamic sub-agents; `grantedSubagents` is populated (self-dispatching)

> **Update 2026-09-20 (ADR-0112):** the `deepagents==0.6.12` pin referenced below is relaxed to a
> `deepagents>=0.7.15` floor (issue 0047 — an `==` in a library binds every consumer). Q2's premise that a
> self-referential sub-agent is *not constructible* (eager roster compilation on 0.6.12) sits in the exact
> area 0.7 reworked and is flagged for re-validation; design B′ (interpreter-driven recursion) holds either way.

Date: 2026-07-15. Status: Accepted. Records the two decisions RAG_Wright owns about the recursive-RLM
rebuild and how it populates `skill_runtime.granted_subagents` (ADR-0003 mirror). Cross-references
GraphWright ADR-0023 for the control-level rule (context only — the repos are decoupled; this ADR does
not wait on it). Grounded in the Deep Agents subagents and dynamic-subagents docs
(docs.langchain.com/oss/python/deepagents).

## Context

The first RLM implementation (T17 chunking, T28 synthesis) was **interpreter + per-slice model calls**:
Python code sliced the working set and dispatched each slice to a single `build_model`/`build_structured
(...).invoke(prompt)` through the model-profile seam — no tools, no per-slice skills, no sub-agents. On
that implementation `grantedSubagents = []` was correct (there were no sub-agents to grant).

Governance settled it (grounded in the Deep Agents docs): a Deep Agents sub-agent is a **named config**
(`name`, `description`, `system_prompt`, `tools`, `skills`, `model`, `response_format`) dispatched **by
name** via `task({subagentType})` from interpreter code, and a `task()` runs a **full agentic loop**
(real tools/skills/isolated context). RLM is an explicitly supported dynamic-subagent workflow: keep the
working set in interpreter variables, select slices, dispatch sub-agents with `task()`, synthesize. So
the sub-agents a recursive RLM dispatches to are **enumerable at authoring time** even though the
dispatch pattern is dynamic. They are Deep Agents configs, **not** ARD-registered capabilities — nothing
new registers in the ARD; `grantedSubagents` lists their names. The empty list was a property of the old
non-recursive build; the recursive rebuild populates it.

## Decision

1. **`grantedSubagents` is a populated list of RAG_Wright's declared Deep Agents sub-agent names.** It is
   authored here, in the manifest, because the RLM decomposition's sub-agents are known at authoring time.
   No new ARD slug/manifest is created for them.
2. **The recursive RLM defines two named sub-agents (Q1):**
   - `rlm_decomposer` — decides one level's split: given an over-budget slice, it returns the sub-slices
     (a fresh agent, fresh context, per dispatch). It does **not** hold the recursion itself; the
     interpreter does (see Q2, corrected).
   - `rlm_slice_worker` — the leaf worker: handles one budget-sized focused slice; **per-slice tool use
     and per-slice skills live here** (it runs a full agentic loop). Specialized per call via the `task()`
     description (summarize for chunking, extract-and-synthesize for synthesis), so one config serves both.
   Both are Deep Agents configs, not ARD capabilities. `grantedSubagents = ["rlm_decomposer",
   "rlm_slice_worker"]` on `rlm_method`, `rlm_chunking`, and `rlm_synthesis`.
3. **The interpreter re-dispatches the decomposer per level (Q2, corrected 2026-07-15 — design B′).**
   The recursion lives in the **interpreter's `eval` code**: a recursive `decompose(node)` workflow holds
   the working set and the recursion stack, dispatches a **fresh `rlm_decomposer`** at each over-budget
   internal node (per-level fresh context — exactly what the RLM paper wants), and an `rlm_slice_worker`
   at each leaf. Arbitrary depth comes from the same interpreter function re-entering itself, not from an
   agent dispatching itself. `grantedSubagents` is unchanged: `["rlm_decomposer", "rlm_slice_worker"]`,
   **without a self-reference** (the decomposer is dispatched *by the interpreter*, never by itself).

   **Why not the decomposer self-dispatching (the original Q2).** A genuinely self-referential sub-agent
   is **not constructible on the pinned `deepagents==0.6.12`**: `SubAgentMiddleware.__init__` compiles its
   whole roster **eagerly** (`middleware/subagents.py`, `__init__` → `_build_task_tool` → the
   `[_compile_spec(spec) for spec in subagents]` comprehension), so a config whose roster contains itself
   re-enters that compile without a base case — infinite recursion at construction; a runnable cannot
   contain itself. (Runtime dispatch *is* name-based — `subagent_graphs[subagent_type]` — but that never
   helps construction.) Grounded empirically on the pinned version: a closed self-reference recurses at
   build time; the only "successful" build was an accidental depth-2 where Python's evaluation order
   broke the cycle before the middleware was attached — not real recursion. Relocating the recursion to
   the interpreter is not a workaround: it is the RLM paradigm stated correctly ("the interpreter selects
   slices and dispatches sub-agents"), and it keeps the wire contract (`grantedSubagents`) stable.
   Recorded so the next reader does not re-derive the dead end.

## Design constraints for the rebuild (recorded so they are not lost)

- **Guarantee the "workflow" trigger by construction.** Dynamic dispatch is prompt-triggered: the Deep
  Agents interpreter treats the word **"workflow"** as the signal to fan work out through code via
  `task()`; without it you get slower one-at-a-time tool-tool dispatch — a **silent under-performance,
  not an error** (it passes tests while doing the wrong thing). Because GraphWright hydrates the RLM
  node's instructions from `interface.success_criterion`, **not** a per-run prompt we write, the trigger
  must be guaranteed **in the RLM skill itself**, not left to a run-time prompt. A test must **fail if a
  run fell back to sequential dispatch** instead of firing code-driven fan-out (same discipline as the
  sigil skill test: prove the dynamic path executed, do not assume it did).
- **Keep the existing `_reduce` fan-in** as the synthesis combine step.
- The rebuild realizes the RLM-requirements: recursive input decomposition, per-slice tool use, per-slice
  skills, dynamic sub-agents. It reopens T15 (skill), T17 (chunking), T28 (synthesis).

## Context — control level (owned by GraphWright, ADR-0023; not enforced here)

An RLM node is **not** a high-control node, and per-slice human-in-the-loop approval is **not** built into
it: RLM is the fast recursive path, and per-slice HITL would defeat its purpose. Governance for a
high-stakes graph sits at RLM's **boundary** (routing into it, consuming its output), not inside the loop.
GraphWright's mapper enforces this by **rejecting a high-control RLM node**, so the RLM skill is built for
the fast path. Recorded as context; RAG_Wright does not enforce it.

## Consequences

- After the rebuild, the three agent_skill manifests carry `skillRuntime.grantedSubagents = ["rlm_decomposer",
  "rlm_slice_worker"]` (the interpreter re-dispatches the decomposer per level, Q2 corrected — **no**
  self-reference). The list is **unchanged** from what was committed, so no manifest re-emit and no
  GraphWright re-verify are forced by the B′ correction.
- New dependencies (landed 2026-07-15, **pinned exactly** — the runtime and dynamic sub-agents are beta,
  so a floating version is a future silent breakage, same discipline as the deepagents pin): **`deepagents==0.6.12`**
  (MIT) and **`langchain-quickjs==0.3.2`** (MIT; backed by `quickjs-rs`/rquickjs — QuickJS is MIT, rquickjs
  MIT/BSD-2 — commercially clean). Dependency-audit note recorded here per the project convention (licenses
  live in ADRs). The rebuild reopens T15/T17/T28.
- Call-site check (2026-07-15, before any retirement): no production `src/` pipeline calls `chunk()` or
  `rlm_synthesize()` — they are graph-bound → **RETIRE** case. Caveats: the `Chunk` *type* (used by
  `embedding.py`/`chunk_write.py`) is a shared contract and is preserved; rlm_chunking's code-deterministic
  boundary/`chunk_id`/gate logic is a T17 contract guarantee, not the RLM method, and stays deterministic;
  the eval harnesses (gate1/gate2) that call `chunk()` are measurement tooling to be repointed, not a
  convert-blocker.
