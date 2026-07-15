# ADR-0018: The RLM method is the orchestrator's system prompt, not a lazy skill source

Date: 2026-07-15. Status: Accepted. Records a grounded finding from the T15 rebuild's opt-in real-model
smoke test: a real model does not reliably follow the RLM method when the method is wired only as a lazy
`skills=` source, because it never reads it. The method is loaded into the orchestrator's **system
prompt** instead. Cross-references ADR-0015/0016 (the rebuild) and ADR-0017 (the dispatch trigger).

## Context

The rebuild's hermetic tests (ADR-0016) script every model to emit the recursion, so they prove the
machinery, not that a real model *chooses* the recursion. The opt-in smoke test was added to prove that
missing property. Its first live run (`google/gemma-4-31b-it`, 2026-07-15) failed with a real signal:

- The model wired the RLM skill via Deep Agents `skills=[...]` (a lazy source, listed as "higher
  priority" but loaded on demand) **never read it** — the transcript shows only `write_todos` + `eval`
  calls, **no** file/skill read. The method never reached the model.
- Working from the interpreter's base "workflow" prompt and the sub-agent roster alone, it produced the
  **flatten-and-hardcode degeneration**: dispatch `rlm_decomposer` once, then invent the slice list in
  JavaScript (`const slices = [...]`) and dispatch workers over that. It looked like it ran while
  skipping the actual recursive decomposition — exactly the silent-under-performance class this rebuild
  exists to eliminate.

Iterating the skill *prose* would have changed nothing, because the model was not reading the skill. The
defect was upstream of wording: **how the method reaches the model.**

## Decision

1. **Load the RLM method (SKILL.md body) into the orchestrator's system prompt** (`build_rlm_agent`
   defaults `system_prompt` to `method_prompt()`), so it is in front of the model on every turn. The RLM
   method is this node's *defining job*, not auxiliary progressive-disclosure content, so it belongs in
   the system prompt. `skills=`/`worker_skills=` remain for genuinely auxiliary per-slice worker skills,
   which a worker may open on demand.
2. **Strengthen SKILL.md** to make the recursion explicit and non-optional: the split is always the
   decomposer's job, the workflow must recurse on `decision.parts` (the decomposer's returned output),
   and a worked **anti-pattern** shows the flatten-and-hardcode form and forbids it.
3. **The smoke test proves derivation, not just "it ran."** The working set is an **opaque handle**
   (`N0`) whose sub-slices only the decomposer reveals; the leaf ids (`N0.0.0` ...) are unknowable in
   advance, so the orchestrator reaches them **only** by recursing on the decomposer's output. Full leaf
   coverage is direct proof of derivation, not a bar lowered to match the observed behavior. The
   decomposer/worker are probes; the orchestrator is the real model under test. A red result is first a
   SKILL.md-quality signal (the machinery is proven hermetically).

## Consequences

- After the fix, the smoke test measured **10/10** on `google/gemma-4-31b-it`: every run dispatched both
  sub-agents via code-driven fan-out and recursed on the decomposer's output to cover all opaque leaves.
  The earlier zero-dispatch non-determinism is removed at its root (the method is always present now, not
  contingent on the model choosing to read a source).
- **Cross-repo note for GraphWright (W10 blocker).** GraphWright assembles the production RLM node. It
  must put the RLM method in the node's **system prompt** (or otherwise guarantee the model sees it every
  turn), **not** rely on a lazy `skills=` attachment — or the same flatten-and-hardcode degeneration
  returns in production, where it is hardest to catch. This is the assembly counterpart to ADR-0017's
  dispatch trigger: both must be applied at node construction for the method to actually fire.
  - **The proof must be a real-model check, not an attachment assertion.** This defect was invisible to
    every hermetic test on both sides precisely because a scripted model does what it is told; only a real
    model reveals whether it *read and followed* the method. So GraphWright's interpreter arm must prove
    the production node with a **real model** — that it recurses on the decomposer's output — and must
    **not** substitute a check that the skill was attached or the system prompt contains the text. "The
    method is present" is necessary, not sufficient; "a real model followed it" is the property. This is
    the one seam neither repo's hermetic tests can cover, and it is where W10 must not be papered over.
- `build_rlm_agent` gains a `system_prompt` parameter (default: the method) and a `method_prompt()`
  helper. No wire-contract or manifest change; `grantedSubagents` is unaffected.
