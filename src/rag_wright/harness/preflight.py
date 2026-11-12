"""What a run will actually build, before anybody pays for it, and how to cut one down to the tier being measured.

**This exists because a docstring was believed over the code.** The first product's routing eval described itself
as *"three variants, a few calls each"* that measured *"ROUTING and nothing else"*, both written in good faith and
neither true: it stubbed the product's operations and not the orchestrator, so every delegating case built the
real orchestrator and every real worker. Measured: 75.5% of a 438.4 s item ran inside one worker, for a decision
the chat-manager had settled 24.8 s in.

**`what_will_run` reads module state, not the compiled graph.** A compiled deep agent keeps its subagents inside
the `task` tool rather than as graph nodes, so walking the graph would report nothing and look like an answer.
What an eval actually changes is which orchestrator factory `build_chat_agent` will call, and that is what this
reads -- **where `build_chat_agent` looks it up** (`harness.agent`), not where it is defined: rebinding the name on
`harness.tiers` changes nothing a run reads, which once scored a routing case on a tool the eval thought it had
removed.

**`only_the_chat_manager` is the cut that makes a routing eval honest.** Most routing scores are settled by the
chat-manager's first call, so running the orchestrator and every worker afterwards spends minutes on work nothing
reads. It replaces the orchestrator with `stub_orchestrator`, which keeps the two things the chat-manager decides
from -- the product's own `orchestrator_description` and the `orchestrator` name -- and answers each brief it is
given as finished work. A product's own operations are the product's to stub inside the same block.

Ported from the first product's eval tooling under ADR-0139's handover (PS-48h).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any

#: What the stub orchestrator says to any brief. **It names the brief and reads as finished work**: a canned reply
#: that ignored the question sent the chat-manager round again three times in a row, 21-40 s per turn, and "I will
#: look into it" is the shape that invites a re-dispatch.
STUB_ORCHESTRATOR_REPLY = (
    "Complete. I worked through the request: {brief}\n\n"
    "Findings: the material that bears on this was read and the answer is above. Coverage: everything in scope "
    "was read. Nothing further is needed -- this request is done."
)


@dataclass(frozen=True)
class WhatWillRun:
    """The tiers a run would construct, given the module state installed right now."""

    #: `"real"` when `build_chat_agent` would call the shipped factory, `"stubbed"` when an eval replaced it. There
    #: is no third answer: absence would be a chat-manager that cannot delegate at all.
    orchestrator: str
    #: The workers that orchestrator would build, in roster order. Empty when it is stubbed: a stub has no
    #: `build_orchestrator` behind it and so reaches no roster.
    workers: tuple[str, ...]

    @property
    def summary(self) -> str:
        return (f"orchestrator: {self.orchestrator}; "
                f"{len(self.workers)} worker(s){': ' + ', '.join(self.workers) if self.workers else ''}")


def what_will_run(definition: Any, context: Any) -> WhatWillRun:
    """Report what a run of `definition`, in `context`, would build, as the harness stands.

    **The worker names are the ones the orchestrator offers**, read off the specs the roster builds -- not the
    factories' function names, which a product is free to choose differently. Building them needs the run's
    scope, which is the product's own type, so the caller passes it; the resources are a stand-in, as in
    `tiers.worker_operations`, because the tools built here are thrown away.
    """
    from rag_wright.harness import agent as agent_module
    from rag_wright.harness import tiers as tiers_module

    real = agent_module.orchestrator_subagent is tiers_module.orchestrator_subagent
    # **Only a real orchestrator reaches the roster.** `build_orchestrator` is what iterates the workers; a stub
    # never calls it, so reporting the roster anyway would describe tiers that will not exist.
    workers = (tuple(w(context, tiers_module._NothingIsNeededHere())["name"] for w in definition.workers)
               if real else ())
    return WhatWillRun(orchestrator="real" if real else "stubbed", workers=workers)


def report(label: str, definition: Any, context: Any) -> WhatWillRun:
    """Print what will run, and return it. Called by an eval runner before it spends anything."""
    plan = what_will_run(definition, context)
    print(f"preflight [{label}]: {plan.summary}", flush=True)
    return plan


def stub_orchestrator(context: Any, resources: Any, settings: Any, *, definition: Any, ledger: Any = None,
                      reply: str = STUB_ORCHESTRATOR_REPLY, **_: Any) -> dict[str, Any]:
    """The orchestrator as the chat-manager SEES it, with nothing underneath.

    **A `CompiledSubAgent`, same as the real one**, so the parent's `task` tool is built the same way. **Its
    description is the product's own** (`definition.prompts.orchestrator_description`): the chat-manager decides
    whether to delegate by reading it, and a reworded stub would change the decision being measured.

    **It records**: the run's dispatch count comes from the ledger, and the ledger sees only a tier carrying
    `RecordTheDecisions` -- a bare stub left it empty, so a dispatch bound read as holding for any behaviour,
    including a run that attempted 24 dispatches. The recorder carries the product's framings, as the real
    orchestrator's does.
    """
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from rag_wright.harness.decisions import DISPATCHER_OF_THE_ORCHESTRATOR, RecordTheDecisions

    class _AnswersTheBrief(GenericFakeChatModel):
        """Replies to what it was actually handed, on every dispatch: a script would run dry."""

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
            brief = next((str(m.content) for m in reversed(messages) if getattr(m, "type", "") == "human"),
                         "the request")
            said = reply.format(brief=brief.strip()[:300])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=said))])

    recording = ([RecordTheDecisions(ledger, tier="orchestrator", framings=definition.framings,
                                     read_framing=definition.read_framing,
                                     dispatched_by=DISPATCHER_OF_THE_ORCHESTRATOR)]
                 if ledger is not None else [])
    runnable = create_agent(_AnswersTheBrief(messages=iter([])), tools=[],
                            system_prompt="You are a stand-in for the orchestrator.", middleware=recording)
    return {"name": "orchestrator", "description": definition.prompts.orchestrator_description,
            "runnable": runnable}


@contextmanager
def only_the_chat_manager(definition: Any, *, reply: str = STUB_ORCHESTRATOR_REPLY) -> Iterator[None]:
    """Within the block, every chat-manager `build_chat_agent` builds delegates to `stub_orchestrator`.

    **Patched where `build_chat_agent` looks it up** (`harness.agent`), and restored on the way out however the
    block ends. `what_will_run` inside the block reports `"stubbed"` and no workers -- which is how an eval proves
    it measures the tier it says it does.
    """
    from rag_wright.harness import agent as agent_module

    shipped = agent_module.orchestrator_subagent
    agent_module.orchestrator_subagent = partial(_stub_for, definition=definition, reply=reply)
    try:
        yield
    finally:
        agent_module.orchestrator_subagent = shipped


def _stub_for(context: Any, resources: Any, settings: Any, *, definition: Any, reply: str,
              ledger: Any = None, **_: Any) -> dict[str, Any]:
    """`stub_orchestrator` for the definition the block was opened with, whatever the builder passes."""
    return stub_orchestrator(context, resources, settings, definition=definition, ledger=ledger, reply=reply)
