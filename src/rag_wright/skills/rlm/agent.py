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

from collections.abc import Callable, Sequence
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

_DECOMPOSER_PROMPT = (
    "You decide how to split ONE working-set slice for a recursive divide-and-conquer. If the slice is "
    "small and focused enough to handle directly, mark it a leaf; otherwise return the sub-slices to "
    "recurse on. Reply as JSON: {\"leaf\": true} for a leaf, or {\"leaf\": false, \"parts\": [...]} to "
    "split. Decide only THIS level; the interpreter re-dispatches you on each sub-slice."
)
_SLICE_WORKER_PROMPT = (
    "You handle ONE focused working-set slice end to end. Use the tools and skills you are given as "
    "needed, then return the result for this slice only. You never see the whole working set."
)

# The canonical interpreter-driven recursive workflow (ADR-0015 Q2 corrected, design B'). Expects a
# `WORKING_SET` binding already in the interpreter (the applying capability loads it as data before this
# runs) and dispatches sub-agents by name via `task()`. Recursion lives HERE, in the interpreter: a fresh
# `rlm_decomposer` decides each level's split, `decompose()` re-enters itself on the returned parts
# (arbitrary depth, the interpreter holds the stack), and `rlm_slice_worker` handles each leaf. The RLM
# skill (SKILL.md) teaches this workflow; the node writes it into the `eval` tool when its request asks
# for a "workflow" (the trigger GraphWright's applier guarantees, requiresDynamicDispatch). It returns
# the per-leaf results plus the depths at which splitting occurred, so the descent is inspectable.
RLM_WORKFLOW_JS = r"""
// Recursive divide-and-conquer over WORKING_SET. Fan the work out to sub-agents in code (a "workflow"),
// never one grinding tool call at a time. The interpreter holds the working set and the recursion stack;
// the model is only ever called on a focused slice.
const _splitDepths = [];  // the depths at which decompose() re-entered itself (proof of descent)
async function decompose(slice, depth) {
  const decision = JSON.parse(await task({
    description: "decompose depth " + depth + ": " + slice,
    subagentType: "rlm_decomposer",
  }));
  if (decision.leaf) {
    const out = await task({
      description: "handle leaf depth " + depth + ": " + slice,
      subagentType: "rlm_slice_worker",
    });
    return [out];
  }
  _splitDepths.push(depth);
  // Fresh decomposer already ran for THIS level; recurse on each returned part (per-level fresh context).
  const handled = await Promise.all(decision.parts.map((p) => decompose(p, depth + 1)));
  return handled.flat();
}
const _leaves = await decompose(WORKING_SET, 0);
JSON.stringify({
  leaves: _leaves,
  leafCount: _leaves.length,
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
        system_prompt=system_prompt if system_prompt is not None else method_prompt(),
        subagents=subagents,
        middleware=[interpreter or CodeInterpreterMiddleware(subagents=True)],
        skills=list(skills) if skills else None,
    )
