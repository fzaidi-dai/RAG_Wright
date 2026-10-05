# ADR-0017: The dynamic-dispatch trigger is a typed `skill_runtime` flag, not a magic word in text

> **Status: PARKED (ADR-0052).** Part of the GraphWright-era RLM-as-interpreter / dynamic-sub-agent runtime, which is parked. The engine's RLM today is authored SKILL.md content built as ordinary software (used by the `rlm_chunking` / `rlm_synthesis` capabilities); there is no interpreter or dynamic-dispatch runtime in this repo.


Date: 2026-07-15. Status: Accepted. The RLM skills' code-driven fan-out ("workflow") trigger is declared
as a typed flag `requires_dynamic_dispatch: bool` on `skill_runtime` (ADR-0003 mirror), and GraphWright's
runtime translates it into whatever trigger phrasing the installed interpreter expects. The trigger word
does not enter the wire contract, and does not go in `interface.success_criterion`.

## Context

Grounding the pinned `langchain-quickjs==0.3.2` (the installed package, not the docs' examples): the
dynamic-dispatch trigger rule lives in the interpreter middleware's base system prompt
(`langchain_quickjs/_prompt.py:251-258`): *"If the user's request mentions running a 'workflow' (or
otherwise uses the word 'workflow'), fan the work out to subagents…"*. It reads the **user's request**
text. `CodeInterpreterMiddleware`'s constructor exposes no system-prompt/instructions override
(`memory_limit, timeout, max_ptc_calls, tool_name, max_result_chars, capture_console, subagents, ptc,
mode, max_snapshot_bytes`). RAG_Wright's skill content is injected into the **system message** (skills
middleware, `deepagents/middleware/skills.py:914,920`) or read as a file — never the "user's request".
So a skill **cannot** inject the trigger from its own authored content on this version.

GraphWright hydrates the RLM node's request/instruction from `interface.success_criterion`. Two rejected
alternatives:
- **Put the trigger word in `success_criterion`.** Fragile: `success_criterion` means "what does done look
  like"; someone later edits that text for good reasons, drops "workflow" as noise, and RLM silently
  falls back to sequential dispatch — the exact silent-under-performance class this rebuild exists to
  kill.
- **Put the literal "workflow" phrasing in a free-text manifest field.** Hardcodes a magic word owned by
  langchain-quickjs into the wire contract; the day that library changes how the trigger reads, every RLM
  manifest must be re-emitted.

## Decision

Declare the requirement as a **typed flag** on `skill_runtime`, the same home as `needs_interpreter` and
`granted_subagents` (an intrinsic execution requirement of the skill, true in every deployment for every
consumer): **`requires_dynamic_dispatch: bool = False`**. It declares that the skill's execution requires
the interpreter's code-driven fan-out to be triggered. It does **not** carry the trigger phrasing — that
lives on GraphWright's side, whose runtime translates the flag into whatever the installed interpreter
version expects. One applier changes when the library changes, not every manifest.

- Mirrored on `SkillRuntime` (`ard.py`), `agent_skill`-only (validated with the rest of `skill_runtime`),
  so a `function` manifest cannot carry it.
- **Implies `needs_interpreter`** (dynamic dispatch is exposed by the interpreter): a validator rejects
  `requires_dynamic_dispatch=True` with `needs_interpreter=False`. Kept a **distinct** field, not
  collapsed — a future interpreter-using skill might not need dynamic-dispatch triggering.
- Populated `true` for the three RLM skills (`rlm_chunking`, `rlm_synthesis`, `rlm_method`), alongside
  their existing `needs_interpreter: true`, `rlm: true`, and `granted_subagents`.

## Cross-repo ownership (this ADR is half the story)

The split is deliberate and each repo owns one side; neither ADR reads as complete alone:
- **RAG_Wright (here):** owns the **typed flag** — its name, its `agent_skill`-only rule, and the
  implies-`needs_interpreter` invariant. The flag says *that* dynamic dispatch is required; it never
  carries *how* the trigger is phrased.
- **GraphWright:** owns the **flag-to-trigger translation** — its runtime reads
  `skillRuntime.requiresDynamicDispatch` and emits whatever phrasing the installed interpreter version
  expects (today: langchain-quickjs's "workflow" word into the RLM node's hydrated request). When the
  interpreter changes how it detects the trigger, that is a one-line change in GraphWright's applier, and
  **no RAG_Wright manifest re-emits**. GraphWright records this on its side (its applier ADR), which
  points back here; this ADR points there. The pair is the whole story.

This commit is the **coordination trigger**: it lands the fourth `skillRuntime` field on the RAG mirror,
so GraphWright must (a) mirror `requires_dynamic_dispatch` on its `RegistryEntry` before its store loads
these re-emitted manifests (else `extra="forbid"` rejects them), and (b) build the applier that consumes
it. The clean sequencing point for GraphWright's mirror is **now, before its applier work**, so its
verification stays green.

## Consequences

- Re-emitted: the three agent_skill manifests carry `skillRuntime.requiresDynamicDispatch: true`;
  GraphWright re-runs the mirror-vs-real-store verification (and mirrors the field first, per above).
- The **fail-if-sequential** test (ADR-0016) remains the proof: it must fail if a run fell back to
  sequential dispatch, confirming the flag actually caused the trigger to fire in production — not just in
  a local test where the request is hand-supplied with "workflow".
- **T15 stays held** until both this field (RAG_Wright) and GraphWright's applier (translating the flag into
  the request) land. The hold is on landing a clean contract, not on a decision.
