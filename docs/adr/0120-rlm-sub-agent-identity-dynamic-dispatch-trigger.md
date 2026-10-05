# ADR-0120: RLM sub-agent identity, and the dynamic-dispatch trigger under hydrated instructions

> **Numbering:** formerly mis-filed as a second `ADR-0015` (heading read `ADR-00XX`); the collision was repaired on
> 2026-10-05 (engine-prep PREP-0.5) by renumbering this record to 0120. Content unchanged.

> **Status: PARKED (ADR-0052).** Part of the GraphWright-era RLM-as-interpreter / dynamic-sub-agent runtime, which is parked. The engine's RLM today is authored SKILL.md content built as ordinary software (used by the `rlm_chunking` / `rlm_synthesis` capabilities); there is no interpreter or dynamic-dispatch runtime in this repo.


Status: Accepted. Owns what grantedSubagents holds and how the RLM skill guarantees dynamic dispatch. The control-level rule for RLM nodes is owned by the GraphWright side and recorded here only as context. Complements the RLM-requirements ADR (recursive decomposition, per-slice tools, per-slice skills, dynamic sub-agents as enforceable tests).

## Context
The RLM (Recursive Language Models) capability is being rebuilt from interpreter-plus-model-calls to interpreter-plus-dynamic-sub-agents. Grounding against the Deep Agents subagents and dynamic-subagents docs settled two questions this side owns.

## Decision
1. grantedSubagents is a populated list of the RLM skill's declared sub-agent names.
Deep Agents sub-agents are declared, named configurations (name, description, system_prompt, tools, model, skills, response_format) passed to create_deep_agent and dispatched by name via task({subagentType}). The identities are static and enumerable at authoring time; only the dispatch pattern (which fire, how often, how deep the recursion) is dynamic at run time.
Therefore grantedSubagents (in the manifest's skill_runtime) is the roster of sub-agent names the RLM skill declares, and it is populated, not empty. The empty-list value recorded for the prior non-recursive implementation is superseded.
The sub-agents are Deep Agents configurations, not separately-registered ARD capabilities. Nothing new registers in the ARD, and grantedSubagents is not a permission list against the registry. This dissolves the RLM-registers-RLM concern: a sub-agent is a named config, not a governed capability.
Two authoring choices this records:

The RLM skill's declared sub-agent names (for example a synthesizer and a per-slice worker) are the grantedSubagents contents.
Whether the recursive decomposer dispatches to itself by name or to a distinct decomposer sub-agent determines whether grantedSubagents includes a self-reference. Both shapes are supported.

2. The dynamic-dispatch trigger is guaranteed by construction and tested.
Deep Agents switches from one-at-a-time task-tool dispatch to code-driven fan-out based on the prompt: the interpreter system prompt treats the word "workflow" as the signal to organize work through task() in code. Without it, dispatch silently falls back to slower sequential handling, which passes tests while under-performing.
Because the RLM node's instructions are hydrated by GraphWright from interface.success_criterion rather than written per invocation, the trigger cannot depend on a run-time prompt. The RLM skill must guarantee the dynamic-orchestration trigger is present by construction, and the rebuild must include a test that fails if a run fell back to sequential dispatch instead of firing code-driven fan-out. Same fail-if-absent discipline as the sigil skill test: prove the dynamic path executed, do not assume it.
Context this rests on (owned by GraphWright, not RAG_Wright)

An RLM interpreter node is not assigned high control; GraphWright's mapper rejects a high-control RLM node. Consequently the RLM skill does not implement per-slice human-in-the-loop approval; governance is applied at RLM's boundary by the graph, not inside the recursive loop. The RLM skill is built for the fast recursive path.

## Consequences

grantedSubagents is populated from the declared sub-agent names once the two authoring choices are made.
The rebuild carries a fail-if-sequential test alongside the recursion, per-slice-tool, and per-slice-skill tests from the RLM-requirements ADR.
The manifest is re-emitted with a populated skill_runtime.grantedSubagents, and GraphWright re-runs the mirror-versus-real-store verification afterward.
