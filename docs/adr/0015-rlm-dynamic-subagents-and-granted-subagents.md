# ADR-0015: RLM is interpreter + dynamic sub-agents; `grantedSubagents` is populated (self-dispatching)

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
   - `rlm_decomposer` — the recursive orchestrator: holds a slice in interpreter variables, decides in
     code to further-slice-and-dispatch or to delegate a leaf, and runs the fan-in reduce (the existing
     `_reduce` is kept) over returned results.
   - `rlm_slice_worker` — the leaf worker: handles one budget-sized focused slice; **per-slice tool use
     and per-slice skills live here** (it runs a full agentic loop). Specialized per call via the `task()`
     description (summarize for chunking, extract-and-synthesize for synthesis), so one config serves both.
   Both are Deep Agents configs, not ARD capabilities. `grantedSubagents = ["rlm_decomposer",
   "rlm_slice_worker"]` on `rlm_method`, `rlm_chunking`, and `rlm_synthesis`.
3. **The decomposer self-dispatches (Q2):** `rlm_decomposer` dispatches `task(subagentType=
   "rlm_decomposer")` on each over-budget sub-slice, so **`grantedSubagents` includes a self-reference**.
   RLM recursion is data-dependent and unbounded; one self-dispatching config expresses arbitrary depth,
   where distinct per-level decomposers cannot. Matches SKILL.md ("apply the *same* three steps") and the
   RLM paper's per-level fresh context.

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
  "rlm_slice_worker"]` (with the decomposer self-referencing). Re-emitted to the shared root; GraphWright
  re-runs the mirror-vs-real-store verification.
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
