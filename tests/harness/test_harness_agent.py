"""The agent a run is conducted by: the chat-manager, built per run from the product's definition.

Ported from RuleWright under ADR-0139's handover (PS-48g); the substrate is `rag_wright.harness.agent`, driven
through the fabric product. RuleWright's tests of its own prompt's wording (its scope, another matter, an instruction
inside a document, when to stop delegating) stay in RuleWright: the words are the product's.

**What these hold** is what makes the agent safe to invoke at all: it carries the product's `context_schema`, so
the entry point accepts it and the scope survives a resume; and no part of the scope reaches anything the model
reads, because a scope in a prompt is one an injected passage can quote back.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from langchain_core.messages import AIMessage

from rag_wright.harness import agent as agent_module
from rag_wright.harness.agent import build_chat_agent
from tests.harness.fabric import FabricContext, _a_context, _fabric, _resources, _settings, scripted


def _build(context: FabricContext | None = None, resources: object = None) -> object:
    return build_chat_agent(context or _a_context(), resources or _resources(), _settings(), definition=_fabric(),
                            model=scripted(AIMessage(content="x")))


def _system_prompt_of(agent: object) -> str:
    inner = getattr(agent.nodes["model"], "bound", agent.nodes["model"])
    function = getattr(inner, "func", None) or getattr(inner, "afunc", None)
    closure = dict(zip(function.__code__.co_freevars, (c.cell_contents for c in function.__closure__), strict=True))
    message = closure["system_message"]
    return str(getattr(message, "content", message) or "")


def test_THE_AGENT_CARRIES_THE_PRODUCTS_CONTEXT_SCHEMA() -> None:
    """An agent compiled with `context_schema=None` accepts `context=` and drops it silently, so `start_run` refuses
    one. This is the other half of that contract."""
    assert _build().context_schema is FabricContext


def test_THE_AGENT_IS_BUILT_FROM_THIS_RUNS_TOOLS(monkeypatch) -> None:
    """Every tool closes over its run's scope. Asserted on the call rather than by reading the compiled graph,
    whose internals are LangGraph's to change."""
    seen: dict = {}
    real = agent_module.chat_manager_tools

    def capture(context, resources, **kwargs):
        seen["context"], seen["resources"] = context, resources
        return real(context, resources, **kwargs)

    monkeypatch.setattr(agent_module, "chat_manager_tools", capture)
    context, resources = _a_context(), _resources()
    _build(context, resources)

    assert seen["context"] is context and seen["resources"] is resources, (
        "the agent was not built from this run's tools, so they are bound to some other scope"
    )


def test_TWO_RUNS_GET_TWO_DIFFERENT_AGENTS() -> None:
    """An agent is scope-specific and must not be pooled; building per run is what makes that true."""
    assert _build() is not _build()


def test_THE_SYSTEM_PROMPT_IS_THE_PRODUCTS_AND_NAMES_NO_PART_OF_THE_SCOPE() -> None:
    """**Tenancy is kept out of everything the model can read**, and the system prompt is the easiest place to
    forget. The substrate interpolates nothing of the scope into it."""
    context = FabricContext(account="acct-7f3a", season="season-91c2", collection="coll-4d0e")
    prompt = _system_prompt_of(_build(context))

    assert _fabric().prompts.chat in prompt, "the chat-manager is not given the product's prompt"
    for part in context.scope:
        assert part not in prompt, f"the system prompt names {part!r}, a part of the scope"


def test_THE_AGENT_CARRIES_THE_ERROR_MIDDLEWARE() -> None:
    """**Without it the library's defaults apply**, and the default is the hazard: `retry_on=(Exception,)` retries
    a scope violation -- re-attempting a leak, with backoff -- and nothing caps a runaway loop. Read off the
    builder's `middleware=` argument, not its characters, so a second middleware beside it does not break this."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(agent_module.build_chat_agent)))
    middleware = [k.value for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "create_deep_agent"
                  for k in n.keywords if k.arg == "middleware"]

    assert middleware, "the agent is built with no middleware argument at all"
    assert any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "error_middleware"
               for c in ast.walk(middleware[0])), "the agent is built without the error middleware"
