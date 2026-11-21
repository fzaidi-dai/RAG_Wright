"""A model told it is repeating itself, which asks again anyway, is made to answer (PS-74).

**PS-73 stopped the tool and not the model.** TexWright's chat-manager, with suppression wired, emitted one
byte-identical call seventeen times: every repeat was answered from the run's record (each reply 614 characters,
the length of a served reply), and the model asked again regardless until the run's call limit. The record saved
the payloads; nothing saved the model calls, and each of those re-reads the whole context.

So once one call has been served from the record a second time -- the model was told, and asked again -- the next
model call goes out with no tools, and the model has to answer from what it has.

Driven through `build_chat_agent`, `worker_middleware` and the orchestrator's builder with a scripted model that
records what it was offered on each call.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

import rag_wright.harness.tiers as tiers_module
from rag_wright.harness import build_chat_agent, start_run
from rag_wright.harness.seen import TheModelWasTold
from rag_wright.harness.workers import worker_middleware
from tests.harness.fabric import _a_context, _fabric, _resources, _settings


class CountingSurface:
    """One direct operation that counts every time it really runs."""

    def __init__(self) -> None:
        self.ran = 0

    def build(self, context: Any, resources: Any, *, only: Any = None) -> list[Any]:
        surface = self

        @tool
        def case_dossier(case: str) -> dict:
            """Read one case's dossier."""
            surface.ran += 1
            return {"case": case, "runs": ["r1", "r2"]}

        return [case_dossier] if only is None or "product.case_dossier" in set(only) else []

    @property
    def exposed_names(self) -> set[str]:
        return {"product.case_dossier"}


def recording(*messages: AIMessage) -> tuple[Any, list[list[str]]]:
    """A scripted model, and the tool names it was offered on each call (`[]` for none)."""
    offered: list[list[str]] = []

    class Recording(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **_kwargs: Any) -> Any:
            offered.append(sorted(getattr(t, "name", "") for t in tools))
            return self

        def bind(self, **_kwargs: Any) -> Any:
            offered.append([])
            return self

    return Recording(messages=iter(messages)), offered


def _call(n: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "case_dossier", "args": {"case": "1867"}, "id": f"c{n}",
                                             "type": "tool_call"}])


def _run(*replies: AIMessage) -> tuple[Any, CountingSurface, list[list[str]]]:
    surface = CountingSurface()
    definition = dataclasses.replace(_fabric(), surface=surface, planning={}, workers=(), routing=None,
                                     repeatable=frozenset({"case_dossier"}), question_argument={})
    model, offered = recording(*replies)
    agent = build_chat_agent(_a_context(), _resources(), _settings(), definition=definition, model=model)
    run = asyncio.run(start_run(agent, _a_context(), message="What cam setting did case 1867 use?"))
    return run, surface, offered


def test_A_MODEL_THAT_ASKS_AGAIN_AFTER_IT_WAS_TOLD_IS_GIVEN_NO_TOOLS() -> None:
    """**TexWright's trace, in small.** Run once, served once, served again: the next call has no tools to call."""
    run, surface, offered = _run(_call(0), _call(1), _call(2), AIMessage(content="The cam setting is not recorded."))

    assert surface.ran == 1
    assert all("case_dossier" in names for names in offered[:3]), offered
    assert offered[3] == [], offered
    assert run.state["messages"][-1].content == "The cam setting is not recorded."


def test_ONE_SERVED_REPEAT_LEAVES_THE_TOOLS_IN_PLACE() -> None:
    """**Told once is not yet ignoring it.** A single repeat is answered from the record and nothing else changes."""
    _, surface, offered = _run(_call(0), _call(1), AIMessage(content="Not recorded."))

    assert surface.ran == 1
    assert all("case_dossier" in names for names in offered), offered


def test_A_SERVED_REPLY_SAYS_SO_AND_A_REAL_ONE_DOES_NOT() -> None:
    """**Countable from outside.** A product counting emitted calls read seventeen served repeats as seventeen runs;
    the marker on the reply is how it tells the two apart without reading the harness's wording."""
    run, _, _ = _run(_call(0), _call(1), _call(2), AIMessage(content="Not recorded."))

    replies = [m for m in run.state["messages"] if isinstance(m, ToolMessage)]
    assert [(m.artifact or {}).get("served_from_record") for m in replies] == [None, 1, 2]


def test_EVERY_TIER_AND_EVERY_WORKER_CARRIES_IT() -> None:
    """**The orchestrator and a worker loop the same way**, so the guard is wherever the record is."""
    assert any(isinstance(m, TheModelWasTold) for m in tiers_module.remembering(_fabric()))
    assert any(isinstance(m, TheModelWasTold) for m in worker_middleware(retry_on=(), message_for=None))
