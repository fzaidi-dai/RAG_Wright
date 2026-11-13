"""Who decides D1, D2 and B1 in the HD-2 and HD-8 comparisons: the tiers as built, Jev alone, and Qwen alone (B1).

**A tier decider is the tier as the harness builds it**, stopped at its decision: the chat-manager at its first
model call (D1), the orchestrator at the call that dispatches workers or answers without them (D2). Tools that run
before that are Loom's canned ones. Since HD-5 and HD-7 the tiers route with the decision model when it is
registered; HD-2 measured them before that existed.

**What this measures is the tier's FIRST action, not its whole turn** (HD-2's caveat, recorded in ADR-0146). A
chat-manager that reads a swatch and then delegates scores as `read_swatch`; an orchestrator that dispatches one worker
and the next after its report scores as the first; one still reading after `ORCHESTRATOR_CALLS` model calls is an
error. The gold labels describe the whole turn, so these understate a tier on `delegate` and on multi-worker
briefs. Jev is called through the capability layer (`ainvoke_model("jev_decision")`).

**A decider returns only its decision** (a label, or the sorted workers), because an eval item counts as flipped
when its repeats differ anywhere in the output, and Jev's probabilities differ on every call. The probabilities go to
`Probabilities`, for HD-3 to choose a threshold from.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
import uuid
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage

from tests.harness.fabric import _a_context, _resources, _settings
from tests.harness.gold.loom import DIRECT, LOOM_PROMPTS, WORKERS, LoomView, d1_options, direct_tools, loom

MODEL = "qwen3.8-27b-modal-or"
#: The orchestrator may read or plan before it dispatches; past this many model calls it has not decided.
ORCHESTRATOR_CALLS = 6

# --- what a tier is shown --------------------------------------------------------------------------------------


def opening(view: dict, text: str) -> str:
    """The turn as the harness puts it in front of a tier: the view a person had open, then their words."""
    return f"{LoomView(**view).render()}\n\n{text}" if view else text


def transcript(turns: list[dict], message: str) -> str:
    earlier = "\n".join(f"{t['role']}: {t['content']}" for t in turns) or "(none)"
    return f"Earlier turns:\n{earlier}\n\nLatest message:\n{message}"


# --- reading a tier's decision off its message -----------------------------------------------------------------


def d1_choice(message: AIMessage) -> str:
    """The chat-manager's D1: the tool it called first (`task` is delegating), or `answer` when it called none."""
    if not message.tool_calls:
        return "answer"
    name = message.tool_calls[0]["name"]
    return "delegate" if name == "task" else name


def d2_workers(message: AIMessage) -> Optional[list[str]]:
    """The orchestrator's D2, or None while it has not decided: the workers its `task` calls name, the workers a
    program it hands `eval` names, or `["none"]` when it answers without calling anything."""
    if not message.tool_calls:
        return ["none"]
    tasked = {c["args"].get("subagent_type", "") for c in message.tool_calls if c["name"] == "task"}
    programs = " ".join(str(c["args"].get("code", "")) for c in message.tool_calls if c["name"] == "eval")
    named = tasked | {w for w in WORKERS if re.search(rf"\b{w}\b", programs)}
    return sorted(named) if named else None


async def decision_of(agent: Any, text: str, decide: Callable[[AIMessage], Any], *, calls: int = 1) -> Any:
    """Run a tier on one turn until `decide` reads a decision off one of its model calls, at most `calls` of them;
    leaving the stream stops the run before the decided tool executes."""
    seen = 0
    # a thread of its own: the harness gives a tier a checkpointer, and repeats must not see each other
    stream = agent.astream({"messages": [{"role": "user", "content": text}]},
                           {"configurable": {"thread_id": uuid.uuid4().hex}}, context=_a_context(),
                           stream_mode="updates")
    async for update in stream:
        for message in itertools.chain.from_iterable((u or {}).get("messages", []) for u in update.values()):
            if isinstance(message, AIMessage):
                seen += 1
                decided = decide(message)
                if decided is not None or seen >= calls:
                    await stream.aclose()
                    return decided
    raise RuntimeError("the tier finished without a model call")


def _on_qwen(definition: Any) -> Any:
    return dataclasses.replace(definition, model_id_for=lambda settings: MODEL)


def tier_d2(*, model: Any = None) -> Callable:
    """D2 by the Loom orchestrator as built (`build_orchestrator`), driven directly with the brief. **Since HD-7 it
    routes with the decision model whenever that is registered**: HD-2 measured it before routing existed. A brief
    the router sends to no worker is read off the run's ledger (`D2` = `none`), the moment the router records it."""
    from rag_wright.harness import build_orchestrator
    from rag_wright.harness.decisions import WhatTheRunDecided

    async def decide(item: Any) -> list[str]:
        ledger = WhatTheRunDecided(points=())
        agent = build_orchestrator(_a_context(), _resources(), _settings(), model=model,
                                   definition=_on_qwen(loom()), ledger=ledger)

        def read(message: AIMessage) -> list[str] | None:
            routed_none = any(d.chose == "none" for run in ledger.runs.values() for d in run.by_point("D2"))
            return ["none"] if routed_none else d2_workers(message)

        workers = await decision_of(agent, opening(item.input["view"], item.input["brief"]), read,
                                    calls=ORCHESTRATOR_CALLS)
        if workers is None:
            raise RuntimeError(f"no dispatch decision in {ORCHESTRATOR_CALLS} model calls")
        return workers
    return decide


def tier_d1(variant: str, *, model: Any = None) -> Callable:
    """D1 by the Loom chat-manager on `variant`, built by `build_chat_agent` as production builds it. **Since HD-5
    it routes with the decision model whenever that is registered** (as `run.py` registers it): HD-2 measured the
    tier before routing existed."""
    from rag_wright.harness import build_chat_agent

    agent = build_chat_agent(_a_context(), _resources(), _settings(), model=model,
                             definition=_on_qwen(loom(variant=variant)))

    async def decide(item: Any) -> str:
        return await decision_of(agent, opening(item.input["view"], item.input["message"]), d1_choice)
    return decide


B1_QUESTION = ("Can the latest message be understood on its own, without the earlier turns? It cannot when it points "
               "back to something only the earlier turns name: 'the second one', 'it', 'those two', 'same for'.")
B1_PROMPT = (f"{B1_QUESTION}\n\nAnswer with exactly one word: self_contained or refers_back.\n\n{{transcript}}")


def qwen_b1() -> Callable:
    """B1 by Qwen with reasoning off. Nothing decides B1 today, so this is the baseline a Qwen rewrite step would be."""
    from rag_wright.models.seam import astream_text

    async def decide(item: Any) -> str:
        reply = await astream_text(MODEL, B1_PROMPT.format(transcript=transcript(item.input["turns"],
                                                                                 item.input["message"])),
                                   max_tokens=16, label="hd2-b1")
        found = re.search(r"self_contained|refers_back", reply)
        if not found:
            raise ValueError(f"no B1 label in {reply!r}")
        return found.group(0)
    return decide


# --- Jev ---------------------------------------------------------------------------------------------------------

D1_INSTRUCTIONS = "What should the assistant do with the person's latest message? Pick the one action that serves it."
#: What each non-tool option means, in the words the chat-manager's prompt uses.
D1_MEANINGS = {
    "delegate": f"Delegate: {LOOM_PROMPTS.orchestrator_description}",
    "ask_the_user": "Ask the person: the message points at something it does not name and nothing on screen shows.",
    "answer": "Reply directly: a greeting, thanks, or a general question that needs no data.",
}
NEEDS_PLANNING = ("Does this message need planning work: matching compositions, comparing mills, checking a swatch "
                  "against a buyer's standard, or several steps?")
_YES_NO = {"true": "yes", "false": "no"}


def d1_criteria(variant: str, *, without: tuple[str, ...] = ()) -> dict[str, str]:
    """Each D1 option of a variant, described by the tool's own description or the meaning above."""
    return {o: (DIRECT[o][0] if o in direct_tools(variant) else D1_MEANINGS[o])
            for o in d1_options(variant) if o not in without}


@dataclasses.dataclass
class Probabilities:
    """Jev's answers per item and repeat, kept beside the eval report (which holds only the decisions)."""

    rows: list[dict] = dataclasses.field(default_factory=list)

    def keep(self, item_id: str, answers: dict) -> None:
        self.rows.append({"item": item_id, "answers": answers})


async def _jev(state: str, questions: dict) -> dict:
    from rag_wright.api import ainvoke_model

    out = await ainvoke_model("jev_decision", {"state": state, "questions": questions}, resources=None)
    return out["answers"]


def jev_d1(variant: str, kept: Probabilities, *, split: bool) -> Callable:
    """D1 by Jev. `split=False`: one `choice` over every option. `split=True`: whether it needs planning (`noul`), then
    a `choice` among the other options, in the same call."""
    questions = ({"plan": {"type": "noul", "instructions": NEEDS_PLANNING, "criteria": _YES_NO},
                  "act": {"type": "choice", "instructions": D1_INSTRUCTIONS,
                          "criteria": d1_criteria(variant, without=("delegate",))}} if split else
                 {"act": {"type": "choice", "instructions": D1_INSTRUCTIONS, "criteria": d1_criteria(variant)}})

    async def decide(item: Any) -> str:
        answers = await _jev(opening(item.input["view"], item.input["message"]), questions)
        kept.keep(item.id, answers)
        if split and float(answers["plan"]["noul"]) >= 0.5:
            return "delegate"
        return answers["act"]["choice"]
    return decide


def jev_d2(kept: Probabilities) -> Callable:
    """D2 by Jev: one `noul` per worker, its description as what a yes means, all in one call."""
    questions = {w: {"type": "noul", "instructions": f"Does this request need the {w}?",
                     "criteria": {"true": description, "false": "the request does not need this"}}
                 for w, (description, _op) in WORKERS.items()}

    async def decide(item: Any) -> list[str]:
        answers = await _jev(opening(item.input["view"], item.input["brief"]), questions)
        kept.keep(item.id, answers)
        return sorted(w for w in WORKERS if float(answers[w]["noul"]) >= 0.5) or ["none"]
    return decide


def jev_b1(kept: Probabilities) -> Callable:
    question = {"b1": {"type": "noul", "instructions": B1_QUESTION, "criteria": _YES_NO}}

    async def decide(item: Any) -> str:
        answers = await _jev(transcript(item.input["turns"], item.input["message"]), question)
        kept.keep(item.id, answers)
        return "self_contained" if float(answers["b1"]["noul"]) >= 0.5 else "refers_back"
    return decide
