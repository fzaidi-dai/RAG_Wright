"""RLM machinery (FR-C.10, `rlm_method`): the Deep Agents assembly the RLM method runs on.

This is the reusable RLM runtime the RLM chunking (T17) and RLM synthesis (T28) capabilities apply.
It is authored software, not a build-tool feature: the RLM node is a Deep Agent whose interpreter
(`CodeInterpreterMiddleware`) holds the working set and runs a recursive `decompose()` *workflow* that
dispatches sub-agents with `task()` — a **fresh `rlm_decomposer`** at each over-budget internal level
(per-level fresh context) and an `rlm_slice_worker` at each leaf. Arbitrary depth comes from the
interpreter re-entering `decompose()`; the interpreter holds the recursion stack.

Two named sub-agents, dispatched by name (ADR-0015):
  - `rlm_decomposer`  — decides one level's split for the slice it is handed; a fresh agent per dispatch.
  - `rlm_slice_worker` — handles one leaf slice; per-slice tool use and per-slice skills live here.

The recursion is driven by the interpreter, **not** by an agent dispatching itself: a self-referential
sub-agent is not constructible on the pinned `deepagents==0.6.12` (its `SubAgentMiddleware.__init__`
compiles its roster eagerly, so a config whose roster contains itself recurses at construction). See
ADR-0015 (Q2, corrected — design B') for the grounded reason and why interpreter-driven recursion is the
faithful realization, not a workaround.

Models resolve through the model-profile seam (T11): the RLM reasoning role (orchestrator + decomposer)
is a Gemma 4 class model (`ModelRole.GENERAL`); the leaf worker's model is chosen by the applying
capability (a smaller model for chunking summaries, the structured-reasoning model for synthesis). No
provider or model flag lives here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Union

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.graph.state import CompiledStateGraph

from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model

# The two named sub-agents (ADR-0015 Q1). Also the exact `grantedSubagents` the manifests declare.
RLM_DECOMPOSER = "rlm_decomposer"
RLM_SLICE_WORKER = "rlm_slice_worker"
GRANTED_SUBAGENTS: tuple[str, str] = (RLM_DECOMPOSER, RLM_SLICE_WORKER)

# LOAD-BEARING, do NOT remove as an "uncontended lock in a serial path" (KI-1, ADR-0020). Two QuickJS
# interpreter runtimes coexisting in one process race on shared Rust state and silently complete without
# dispatching ~half the time, with zero exceptions. This process-wide semaphore serializes the FULL
# interpreter-session lifetime (build -> run -> teardown) so no two RLM runtimes are ever alive at once.
# It is uncontended (zero cost) while ingestion is serial; it makes the safe behaviour the DEFAULT so that
# adding concurrency later turns a silent-correctness failure into a visible-performance one (slower, not
# wrong). It lifts only when the upstream coexistence bug is fixed (ADR-0020 exit path). Task T35 designs
# the real concurrent batch; this is the always-on correctness floor beneath it, not throughput tuning.
_INTERPRETER_SEMAPHORE = threading.BoundedSemaphore(1)


@contextmanager
def rlm_interpreter_session(
    *, ptc: Sequence[BaseTool] = ()
) -> Iterator[CodeInterpreterMiddleware]:
    """Own the process for exactly one RLM interpreter session (KI-1, ADR-0020).

    Holds `_INTERPRETER_SEMAPHORE` from before the interpreter runtime is built (the middleware is created
    here; its QuickJS runtime is built lazily on first `eval`, inside the lock) until after it is torn down
    (the registry is closed here, deterministically, not on GC timing) — the whole coexistence window, not
    just the dispatch call. Build the RLM agent with the yielded middleware and run it inside the `with`;
    do parsing/String work outside it. Reentrant call from within one session would deadlock — one session
    per call stack, which the discoverer/extractor honour (they invoke once, then parse outside).

    `ptc` are Programmatic-Tool-Calling tools exposed **inside** the interpreter as `tools.<camelCase>()`
    and never as top-level tools — this is how the working set is delivered as a JS value that stays out of
    the model's context (`working_set` → `tools.workingSet()`, T36 / GraphWright working-set contract).
    """
    _INTERPRETER_SEMAPHORE.acquire()
    interpreter = CodeInterpreterMiddleware(subagents=True, ptc=list(ptc) or None)
    try:
        yield interpreter
    finally:
        try:
            interpreter._registry.close()  # tear the QuickJS runtime down inside the lock (no coexistence)
        finally:
            _INTERPRETER_SEMAPHORE.release()

_DECOMPOSER_PROMPT = (
    "You decide how to split ONE working-set slice (a list of items) for a recursive divide-and-conquer. "
    "If the slice is small and focused enough to handle directly, mark it a leaf; otherwise return the "
    "split offsets that partition it into coherent contiguous groups. Reply as JSON: {\"leaf\": true} for "
    "a leaf, or {\"leaf\": false, \"cuts\": [i, j, ...]} where each cut is an ascending index into the "
    "slice at which a new group begins. The cuts partition the slice, so no item is lost. Decide only THIS "
    "level; the interpreter re-dispatches you on each group."
)
_SLICE_WORKER_PROMPT = (
    "You handle ONE focused working-set slice end to end. Use the tools and skills you are given as "
    "needed, then return the result for this slice only. You never see the whole working set."
)

# The canonical interpreter-driven recursive workflow (ADR-0015 Q2 corrected, design B'). Reads the
# working set from the runtime PTC tool `tools.workingSet()` (T36) — a JS value that never enters context —
# and dispatches sub-agents by name via `task()`. Recursion lives HERE, in the interpreter: a fresh
# `rlm_decomposer` decides each level's split, `decompose()` re-enters itself on the returned parts
# (depth capped at _MAX_DEPTH = 3, the interpreter holds the stack), and `rlm_slice_worker` handles each leaf. The RLM
# skill (SKILL.md) teaches this workflow; the node writes it into the `eval` tool when its request asks
# for a "workflow" (the trigger GraphWright's applier guarantees, requiresDynamicDispatch). It returns
# the per-leaf results plus the depths at which splitting occurred, so the descent is inspectable.
RLM_WORKFLOW_JS = r"""
// Recursive divide-and-conquer over a working set of items. Read it from the runtime tool (a JS value
// that stays in the interpreter and never enters the model's context), then fan the work out to
// sub-agents in code (a "workflow"), never one grinding tool call at a time. The interpreter holds the
// working set and the recursion stack; the model is only ever called on a focused slice.
const workingSet = await tools.workingSet();  // [{id, ...}, ...] delivered by the runtime, never in context
// LOAD-COMPLETENESS ASSERTION (T37): verify you loaded the WHOLE delivered set before anything else. The
// size comes from the runtime (a scalar it cannot under-read); if the load is short, fail loud rather than
// silently working over a truncated set — the coverage tail below only guarantees coverage over what you
// loaded, so an under-read here is a silent evidence drop nothing downstream catches.
const _delivered = await tools.workingSetSize();
if (workingSet.length !== _delivered) {
  throw new Error("LOAD UNDER-READ: loaded " + workingSet.length + " of " + _delivered + " delivered items");
}
const _MAX_DEPTH = 3;                // hard cap on recursion — beyond this, force leaf (no runaway splits)
const _splitDepths = [];             // the depths at which decompose() re-entered itself (proof of descent)
const _handled = new Set();          // ids of working-set items a leaf worker covered
async function decompose(items, depth) {
  if (items.length === 0) return [];    // empty slice — nothing to dispatch (no-op leaf)
  if (depth >= _MAX_DEPTH) {
    for (const it of items) _handled.add(it.id);
    return [await task({ description: "handle leaf depth " + depth, subagentType: "rlm_slice_worker" })];
  }
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
""".strip()


_SKILL_PATH = Path(__file__).parent / "SKILL.md"


def method_prompt() -> str:
    """The RLM method (SKILL.md body, YAML frontmatter stripped) as the orchestrator's instructions.

    The method is loaded into the orchestrator's **system prompt**, not wired as a lazy `skills=` source:
    grounded on the pinned stack (2026-07-15), a real model does NOT proactively open a lazy skill source
    before writing its `eval` workflow, so a lazy method never reaches it and it improvises a flat split.
    The RLM method IS this node's defining job, so it belongs in the system prompt, always in front of the
    model. (`skills=`/`worker_skills=` remain for auxiliary per-slice worker skills, which the worker may
    open on demand.) See ADR-0018.
    """
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):  # strip YAML frontmatter
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return text.strip()


_ModelArg = Union[BaseChatModel, str, None]


def _resolve_model(model: _ModelArg, default_role: ModelRole) -> BaseChatModel:
    """Resolve a model argument to a concrete `BaseChatModel` instance.

    An instance passes through (tests inject fakes here); a string is a model id built through the seam;
    `None` falls back to the profile for `default_role`. Always an instance — `create_deep_agent` would
    otherwise resolve a bare id through its own provider path, which our OpenRouter-via-`langchain_openai`
    seam does not use.
    """
    if isinstance(model, BaseChatModel):
        return model
    return build_model(model or model_for(default_role))


def decomposer_config(*, model: _ModelArg = None) -> SubAgent:
    """The `rlm_decomposer` sub-agent: decides one level's split; a fresh agent per dispatch.

    Reasoning work, so it defaults to the Gemma 4 class RLM role (`ModelRole.GENERAL`). It carries no
    roster of its own — the interpreter, not the decomposer, drives the recursion (ADR-0015 Q2).
    """
    return {
        "name": RLM_DECOMPOSER,
        "description": "Decides how to split one working-set slice for recursive RLM decomposition.",
        "system_prompt": _DECOMPOSER_PROMPT,
        "model": _resolve_model(model, ModelRole.GENERAL),
    }


def slice_worker_config(
    *,
    model: _ModelArg = None,
    system_prompt: str = _SLICE_WORKER_PROMPT,
    tools: Sequence[Union[BaseTool, Callable[..., Any], dict[str, Any]]] = (),
    skills: Sequence[str] = (),
) -> SubAgent:
    """The `rlm_slice_worker` sub-agent: handles one leaf slice, with per-slice tools and skills.

    The applying capability supplies `tools`, `skills`, and (via the `task()` description) the per-call
    specialization (summarize for chunking, extract-and-synthesize for synthesis), so one config serves
    both. The worker model is the applying capability's choice; it defaults to the RLM role.
    """
    config: SubAgent = {
        "name": RLM_SLICE_WORKER,
        "description": "Handles one focused leaf slice end to end, using per-slice tools and skills.",
        "system_prompt": system_prompt,
        "model": _resolve_model(model, ModelRole.GENERAL),
        "tools": list(tools),
    }
    if skills:
        config["skills"] = list(skills)
    return config


def build_rlm_agent(
    *,
    reasoning_model: _ModelArg = None,
    decomposer_model: _ModelArg = None,
    worker_model: _ModelArg = None,
    worker_system_prompt: str = _SLICE_WORKER_PROMPT,
    worker_tools: Sequence[Union[BaseTool, Callable[..., Any], dict[str, Any]]] = (),
    worker_skills: Sequence[str] = (),
    tools: Sequence[Union[BaseTool, Callable[..., Any], dict[str, Any]]] = (),
    system_prompt: Optional[str] = None,
    skills: Optional[Sequence[str]] = None,
    interpreter: Optional[CodeInterpreterMiddleware] = None,
) -> CompiledStateGraph:
    """Assemble the RLM Deep Agent: an interpreter orchestrator over the two named sub-agents.

    The orchestrator holds the working set in the interpreter and runs the recursive `decompose()`
    workflow (`RLM_WORKFLOW_JS`) via the `eval` tool, dispatching `rlm_decomposer` per level and
    `rlm_slice_worker` per leaf. `reasoning_model` is the orchestrator; `decomposer_model`/`worker_model`
    default to it / the profile. `system_prompt` defaults to the RLM method (`method_prompt()`), always in
    front of the orchestrator; `skills`/`worker_skills`/`worker_tools` are auxiliary (the leaf worker's).
    Tests inject fake models per role.

    Returns the compiled agent. This is the reference assembly RAG_Wright's own tests and evals run and
    the applying capabilities (T17, T28) build on; the graph half (GraphWright) assembles the production
    node equivalently from the same skill and `grantedSubagents`.
    """
    orchestrator = _resolve_model(reasoning_model, ModelRole.GENERAL)
    subagents: list[SubAgent] = [
        decomposer_config(model=decomposer_model if decomposer_model is not None else reasoning_model),
        slice_worker_config(
            model=worker_model,
            system_prompt=worker_system_prompt,
            tools=worker_tools,
            skills=worker_skills,
        ),
    ]
    return create_deep_agent(
        model=orchestrator,
        tools=list(tools),
        system_prompt=system_prompt if system_prompt is not None else method_prompt(),
        subagents=subagents,
        middleware=[interpreter or CodeInterpreterMiddleware(subagents=True)],
        skills=list(skills) if skills else None,
    )
