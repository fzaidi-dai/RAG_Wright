"""The agent a run is conducted by (T-5a.4b, §2.1, AR-6, ANFR-2).

**One tier for now, and that is the honest shape of this slice.** `AGENT-SPEC.md` §2 describes three —
chat-manager, orchestrator, workers — and T-5a.11 builds them. Building the tiers before anything has run
end to end would be designing a hierarchy against a prediction; this is the chat-manager alone, holding the
read surface directly, which is exactly what §2.1 says it does for requests that need no planning.

**Built per run, not per process.** Every tool in the manifest closes over the run's context, so an
agent assembled from one is tenant-specific and must not be cached or pooled across runs — `tools.py`
records the consequence, and this is where it binds.

**The system prompt is a guardrail, not decoration.** It tells the model that its scope is one matter and
that it cannot widen it, because the alternative is a model that *tries* — and a tool that refuses is a
better answer than a prompt that persuades. The prompt never carries the tenant: `AR-5` keeps tenancy out
of everything the model can read, and a workspace id in a system prompt is a workspace id an injected
passage can quote back.
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver

from rag_wright.harness.confirm import ConfirmTheView
from rag_wright.harness.decisions import RecordTheDecisions, WhatTheRunDecided
from rag_wright.harness.dispatch import dispatch_bound
from rag_wright.harness.errors import error_middleware
from rag_wright.harness.filesystem import TheSubjectIsNotAFilesystem
from rag_wright.harness.memory import (
    MEMORY_SOURCES,
    build_memory_backend,
    memory_permissions,
)
from rag_wright.harness.models import agent_chat_model
from rag_wright.harness.scope import RunScope
from rag_wright.harness.tiers import chat_manager_tools, orchestrator_subagent
from rag_wright.harness.withhold import WITHHELD_FROM_CHAT, withhold

#: **What a chat-manager turn may produce, and why there is a number here at all** (T-5a.14d). With
#: reasoning off the measured turn was 42 output tokens; a cap of 2048 is ~50x that, so it cannot
#: truncate real work, and it bounds the runaway the resume block already records (8192 tokens, 104.5s).
#: A turn that can outlive its own deadline makes the deadline decorative and the retry pointless.
CHAT_TURN_TOKEN_CAP = 2048


def chat_thinking() -> Any:
    """The conversation tier does not reason, and this is measured rather than preferred.

    Same question, same 10 tools, same model: reasoning ON took **34.8s** and 1,812 output tokens (1,905
    of them reasoning); reasoning OFF took **4.3s** and 42 tokens. **Both chose `list_matter_documents`.**
    The tier's job is `D1` -- answer directly or delegate -- which `robustness-before-speed.md` §2a
    describes as a classification, and 1,905 tokens of deliberation bought nothing.

    It is a MIDDLEWARE because a `ProviderProfile` keys on the model and both tiers run the same one;
    the same reason `withhold.py` exists.
    """
    from rag_wright.harness.thinking import HowMuchToThink

    return HowMuchToThink(reasoning=False, tier="chat-manager", max_tokens=CHAT_TURN_TOKEN_CAP)


def build_chat_agent(
    context: RunScope,
    #: The product's own per-run resource bundle, opaque here (T-7.58): the substrate hands it
    #: to the product's tools and workers and never reads a field off it.
    resources: Any,
    settings: Any,
    *,
    model: Any = None,
    #: The product's agent: its tool surface, planning classification, worker roster and context type.
    #: **Required, with no default** -- see `tiers.build_orchestrator` for why the default had to go.
    definition: Any,
    checkpointer: Any = None,
    store: Any = None,
    ledger: Any = None,
) -> Any:
    """The chat-manager for one run, holding the manifest bound to that run's matter.

    **The model is resolved through DeepAgents**, not built here: `AGENT_MODEL_SPEC` plus a registered
    `ProviderProfile` is the only combination that applies our OpenRouter configuration (T-5a.4a-ii found
    that `init_chat_model` silently ignores the profile).

    **A `context_schema` is required, not conventional.** It comes from `definition.context_schema` --
    the product's own context type, which the substrate never names -- and `start_run` refuses an agent
    without one, because an agent compiled with `context_schema=None` accepts `context=` and drops it
    silently.

    **The checkpointer defaults to in-memory, which is a real limitation.** `run-contract.md` obligation 1
    says a run outlives the view that started it, and an in-memory saver does not outlive the process. A
    durable checkpointer is T-5a.4c's business, where re-attaching to a run by id is the requirement that
    forces it; passing one here is how that lands.
    """

    # **One ledger for the whole run, shared by every tier** (T-5a.13a). A dispatch is recorded by the
    # worker and attributed to the orchestrator, and `D1` is taken up here -- so a ledger per tier could
    # never say that the worker this run re-dispatched had already answered. Built here rather than held
    # per process because an agent is built per run: the ledger lives and dies with the run it describes,
    # which is also what keeps one matter's briefs out of another's memory.
    decisions = (ledger if ledger is not None
                 else WhatTheRunDecided(points=definition.decision_points))

    memory: dict[str, Any] = {}
    if store is not None:
        # **Memory is granted, never assumed.** With no store there is no durable memory at all, which is
        # the right behaviour for a test and for any caller that has not decided where memory lives --
        # rather than silently falling back to a namespace shared by every tenant, which is exactly what
        # `StoreBackend(namespace=None)` would do (T-5a.7a).
        memory = {
            "backend": build_memory_backend(store),
            "memory": MEMORY_SOURCES,
            "permissions": memory_permissions(),
        }

    return create_deep_agent(
        model=model if model is not None else agent_chat_model(
            settings, definition.model_id_for(settings)),
        # **The DIRECT half only** (T-5a.11a, §2.1). The orchestrator below holds all of it, so nothing is
        # unreachable -- but a listing must not pay for three model calls.
        tools=chat_manager_tools(context, resources, definition=definition),
        subagents=[orchestrator_subagent(context, resources, settings, ledger=decisions,
                                         definition=definition)],
        system_prompt=definition.prompts.chat,
        context_schema=definition.context_schema,
        # **`AR-14`'s four levels, and `AR-19`'s one exception.** Without this the library's defaults
        # apply: `retry_on=(Exception,)` would retry a `ScopeViolation` -- re-attempting a leak, with
        # backoff -- and nothing would cap a runaway loop.
        middleware=[
            # **Per-AGENT, not per-model** (T-5a.11f). `HarnessProfileConfig.excluded_tools` keys on the
            # model, and both tiers run the same one -- it would take the interpreter from the
            # orchestrator too, which is where T-5a.12 needs it.
            withhold(*WITHHELD_FROM_CHAT),
            chat_thinking(),
            # **A count, not a request** (T-5a.14e). Measured: this tier delegated 3 times with reasoning
            # on and 24 with it off, each time after reading "Complete -- nothing further is needed".
            # Both numbers are the model's judgement; this one is not.
            dispatch_bound(),
            RecordTheDecisions(decisions, tier="chat-manager",
                               read_framing=definition.read_framing),  # the top tier: nobody dispatches it
            TheSubjectIsNotAFilesystem(signposts=definition.prompts), ConfirmTheView(), *error_middleware(retry_on=definition.retry_on,
                             message_for=definition.message_for),
        ],
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        **memory,
    )
