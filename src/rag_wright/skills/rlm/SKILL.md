---
name: rlm
description: >
  The recursive-language-model (RLM) divide-and-conquer method: when a working set is too large for
  one context window, load it into the code interpreter as data, then write a recursive workflow that
  dispatches the work to sub-agents in code (a fresh decomposer per level, a worker per leaf) and
  combines their results, so the model never attends over the full volume. Applied by the RLM chunking
  capability (ingestion) and the RLM synthesis capability (query).
---

# RLM: divide and conquer over a working set too large for one prompt

This skill teaches a **method**, not a behavior. It is authored software (FR-C.10), not a build-tool
feature. Two capabilities apply it with their own contracts and tests: **RLM chunking** (FR-I.1,
ingestion) and **RLM synthesis** (FR-Q.5, query). This document is deliberately general; every
guarantee an application needs (determinism, boundary validation, gating, idempotence) is the
**applying capability's** job, not the method's (see "What this skill does NOT own").

## The problem

A prompt has a bounded context window. Real working sets, a whole parsed document or a large candidate
set of retrieved chunks, routinely exceed it, or fit but degrade quality when the model must attend
over everything at once. Stuffing the whole volume into one call is the failure mode this method avoids.

## The method: interpreter holds the whole → a recursive workflow dispatches sub-agents → combine

1. **Get the working set from the runtime, as data.** Call `const workingSet = await tools.workingSet();`
   — the runtime returns the full input as ordinary interpreter values (strings, lists, dicts), *not*
   into a prompt. The interpreter, not the model, holds the state and the recursion stack, and is not
   bounded by a context window. Nothing about the whole volume is sent to a model. The working set is
   never in your context; you only ever hold it as an interpreter variable.

2. **Write a recursive `decompose()` workflow in code that dispatches sub-agents.** This is a
   **workflow**: fan the work out to sub-agents with `task()` from interpreter code, never one grinding
   tool call at a time. `decompose(slice)` does one of two things:
   - If the slice is small and focused enough, dispatch it to an **`rlm_slice_worker`** (a leaf): a
     full agentic loop that handles that one slice with its own tools and skills, and sees only that
     slice.
   - Otherwise dispatch a **fresh `rlm_decomposer`** to decide this level's split, then **call
     `decompose()` again on each returned sub-slice.** The recursion lives here, in the interpreter:
     the same function re-enters itself to arbitrary depth, and each level's decomposer is a fresh
     agent with fresh context. A model is only ever called on a focused slice, never on the whole.

   The recursion is driven by the **interpreter**, not by an agent dispatching itself: on this runtime
   a sub-agent cannot dispatch to itself (a self-referential agent is not constructible). The
   interpreter re-dispatching a fresh `rlm_decomposer` per level *is* the recursion, and it delivers
   the per-level fresh context the method wants.

3. **Combine the results in code, recursively if needed.** Collect the per-leaf outputs and reduce them
   in code (a fan-in). When the combined intermediate is itself too large, apply the same reduce
   recursively, so a model is called on the *reduced* material, never on the raw whole.

The invariant across all three steps: **the model is only ever called on a small, focused slice; the
interpreter holds the whole and drives the recursion.**

### The canonical workflow (write it this way)

The recursive descent, written into the `eval` tool. Read the working set from the runtime tool
`tools.workingSet()` — it returns the whole working set as a JavaScript value that lives in the
interpreter and never enters your context. Do **not** expect the working set in your prompt; call the
tool.

Write this workflow **exactly** — the decomposer dispatch MUST carry the item count (`over N items`) or
the decomposer cannot return index cuts and you fall back to a flat, non-recursive run:

```javascript
// Recursive divide-and-conquer over a working set of items. Read it from the runtime tool (a JS value
// that stays in the interpreter and never enters the model's context), then fan the work out to
// sub-agents in code (a "workflow"), never one grinding tool call at a time. The interpreter holds the
// working set and the recursion stack; the model is only ever called on a focused slice.
const workingSet = await tools.workingSet();  // [{id, ...}, ...] delivered by the runtime, never in context
const _splitDepths = [];             // the depths at which decompose() re-entered itself (proof of descent)
const _handled = new Set();          // ids of working-set items a leaf worker covered
async function decompose(items, depth) {
  const decision = JSON.parse(await task({
    description: "decompose depth " + depth + " over " + items.length + " items",
    subagentType: "rlm_decomposer",
  }));
  if (decision.leaf) {
    for (const it of items) _handled.add(it.id);
    return [await task({ description: "handle leaf depth " + depth, subagentType: "rlm_slice_worker" })];
  }
  _splitDepths.push(depth);
  // decision.cuts partition `items` into contiguous groups (no item lost); recurse on every group.
  const bounds = [0, ...decision.cuts, items.length];
  const groups = [];
  for (let i = 0; i < bounds.length - 1; i++) groups.push(items.slice(bounds[i], bounds[i + 1]));
  const handled = await Promise.all(groups.map((g) => decompose(g, depth + 1)));
  return handled.flat();
}
const _leaves = await decompose(workingSet, 0);
// COVERAGE TAIL (structural, in-interpreter): the code holds EVERY item, so it guarantees coverage even
// if the recursion missed a deep leaf out of context — a silent drop otherwise (T37). Any uncovered item
// is dispatched now, not dropped. This is code checking coverage, not the model asked to be thorough.
const _missed = workingSet.filter((it) => !_handled.has(it.id));
if (_missed.length) {
  for (const it of _missed) _handled.add(it.id);
  _leaves.push(await task({ description: "cover " + _missed.length + " missed items", subagentType: "rlm_slice_worker" }));
}
JSON.stringify({
  leaves: _leaves,
  leafCount: _leaves.length,
  covered: _handled.size,
  total: workingSet.length,
  missed: _missed.length,
  maxSplitDepth: _splitDepths.length ? Math.max(..._splitDepths) : -1,
});
```

**These rules are not optional. Follow them exactly:**

1. **The split is the decomposer's job, never yours.** How to partition each level MUST come from a
   `rlm_decomposer` return value (`decision.cuts`), obtained by dispatching the decomposer. You do not
   decide the split yourself and you do not know the grouping in advance; only the decomposer does.
2. **Recurse on the decomposer's output.** When the decomposer returns `cuts`, slice `items` into those
   groups and call `decompose()` again on **each group**. A group may itself split, to arbitrary depth.
   Stop a branch only when the decomposer marks that slice a leaf (`decision.leaf === true`), then dispatch
   a `rlm_slice_worker`.
3. **One `decompose()` per node, one decomposer dispatch per node.** A single decomposer call for the
   whole working set is wrong: that is a flat split, and it defeats the method.
4. **The coverage tail guarantees completeness — never skip it, and never rely on it as a licence to be
   incomplete.** After the recursion, the tail (above) diffs the working set against `handled` and
   dispatches any item the recursion missed, in interpreter code, before returning. This is *code checking
   coverage against the whole working set you hold*, not you being asked to be thorough — the structural
   guarantee the method needs, because out of context a missed deep leaf is a silent drop that still
   reports success. Still write a complete recursion; the tail is the safety net, not the method.

**Anti-pattern (do NOT do this):**

```javascript
// WRONG: calling the decomposer once, then hardcoding the slice list and skipping the recursion.
await task({ description: "split it", subagentType: "rlm_decomposer" });
const slices = [ {id: "part_a", ...}, {id: "part_b", ...}, {id: "part_c", ...} ];  // <-- you invented these
for (const s of slices) { await task({ subagentType: "rlm_slice_worker", description: "handle " + s.id }); }
```

That is the failure this method exists to prevent: it looks like it ran, but the slices came from you, not
from a recursive decomposition, so the working set was never actually divided and conquered. If you find
yourself writing a literal array of slices, stop: you should be recursing on `decision.parts` instead.

## How the two capabilities apply it

- **RLM chunking (FR-I.1).** Working set = one whole parsed document. `decompose` splits along topic /
  section / chapter boundaries into semantically coherent, capped chunks; each leaf worker summarizes
  its chunk. The applying capability keeps the boundary and `chunk_id` computation deterministic
  (that stays a separate, exact function; it is not the RLM method's job).
- **RLM synthesis (FR-Q.5).** Working set = the candidate chunks a query retrieved. `decompose` slices
  the candidate set; each leaf worker extracts the query-relevant facts from its slice; the code-side
  fan-in reduce combines the extracts, so synthesis never attends over the full chunk volume.

## The dynamic-dispatch trigger

This workflow only fans out if the run is triggered for code-driven dispatch. The skill declares that
requirement (`skillRuntime.requiresDynamicDispatch`); the runtime that hosts the RLM node applies the
trigger so this `eval` workflow fires, rather than a slower one-slice-at-a-time fallback. The skill does
not carry the trigger phrasing itself.

## What this skill does NOT own (deferred to the applying capability)

- **Determinism and reproducibility** — temperature zero or structured output, and stable ids. (RLM
  chunking owns this for `chunk_id`s and boundaries, in a separate deterministic function.)
- **Boundary validation** — checking that produced slices are well-formed and within caps.
- **Gating** — content-hash gating so unchanged input does no work; incremental, resumable runs.
- **Model choice** — which model each role uses, via the model-profile seam (never a hardcoded flag).

The method is the shape of the computation; the capability supplies the contract, the guarantees, and
the tests. Keep this file about the shape.
