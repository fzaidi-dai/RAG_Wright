# ADR-0016: RLM is defined by required capabilities, enforced as tests

Date: 2026-07-15. Status: Accepted. Defines RLM by what an implementation must DO, enforced as
fail-if-absent tests, so the rebuild cannot drift the way the first implementation did. This ADR is the
RLM-requirements record on the RAG_Wright side; ADR-0015 records sub-agent identity and the workflow
trigger, not the required capabilities.

## Context

The prior RLM implementation was **interpreter-plus-model-calls**: one-level split, a stateless per-slice
model call, no per-slice tools, skills, or sub-agents; the only recursion was the `_reduce` fan-in over
produced notes (see the code-cited account: `rlm_chunking.chunk`/`_summarize_all`,
`rlm_synthesis.rlm_synthesize_async`/`_reduce`, `SeamSummarizer`/`SeamSynthesizer`). That satisfied the
`rlm`/`needsInterpreter` markers and the seams **but not the paradigm**. This ADR defines RLM by what it
must do, so an implementation lacking any of these is **not** an RLM implementation, regardless of markers.

## Required capabilities

1. **Recursive input decomposition.** A dispatched unit that is itself too large or complex is
   decomposable further by the same method, to arbitrary depth. Recursion on the way **down** (decomposing
   inputs), not only the `_reduce` fan-in on the way up.
2. **Per-slice tool use.** A unit handling a slice can use tools mid-handling, not a single forward model
   call.
3. **Per-slice skill and behavior.** A slice-handler can carry its own skill or behavior.
4. **Dynamic sub-agents.** Slices are handled by dynamically dispatched Deep Agents sub-agents (`task()`)
   with their own context and loop, which is what makes 1–3 possible.

The existing `_reduce` fan-in is **retained**; it is the ascent that complements the newly-required descent.

## Enforcement (fail-if-absent tests)

- A test that **fails if no recursion occurs**: a working unit large enough to force at least one level of
  further decomposition, asserting the decomposition happened.
- A test asserting a slice-handler **can invoke a tool**.
- A test asserting a slice-handler **can load a skill**.
- A test that **fails if a run fell back to sequential dispatch** instead of code-driven fan-out (the
  "workflow" trigger actually fired).

A stateless-model-call implementation must **fail** these. **Markers alone are never sufficient to pass.**
