"""PS-32 (ADR-0139): the agent harness substrate, the engine's third public tier.

A product builds its agent on this: a chat-manager tier a person talks to, an orchestrator that plans, and
workers it dispatches, on Deep Agents / LangGraph. The product declares what only it knows -- its tools
(`ToolSurface`), which of them need planning, its workers, its run context, its prompts, its rules -- in an
`AgentDefinition`, and the substrate derives the rest. It names no domain, no model, no vendor and no tenancy
shape: the run's context satisfies `RunScope` (a namespace prefix the product supplies), the model comes from
an engine profile id, and the product passes its checkpointer and memory store in.

**One door.** Everything the agent can do arrives through the product's tool surface, and every agent invocation
goes through `start_run` / `resume_run` / `stream_run`. The harness reaches the rest of the engine only through
`rag_wright.api` (an import contract in `pyproject.toml`), and nothing else in the engine imports the harness.
`__all__` is the public surface; anything not named here is the harness's own.
"""

from rag_wright.harness.accounting import accounted_run
from rag_wright.harness.agent import build_chat_agent
from rag_wright.harness.clarify import clarification_tool
from rag_wright.harness.confirm import ConfirmTheView
from rag_wright.harness.decisions import ANY_WORKER, DecisionPoint, RecordTheDecisions, WhatTheRunDecided
from rag_wright.harness.dispatch import DISPATCHES_PER_RUN
from rag_wright.harness.definition import AgentDefinition, AgentPrompts, ToolSurface
from rag_wright.harness.errors import ModelAttemptTooSlow, ModelTooSlow, ModelTurnTooSlow, error_middleware
from rag_wright.harness.events import RunEvent, translate, what_was_finished
from rag_wright.harness.faults import Fault, InjectFaults
from rag_wright.harness.grading import GRADER_ERROR, GradeThisTier, Verdict, grading_middleware, rule_tools
from rag_wright.harness.interpreter import bridged_tools, js_name
from rag_wright.harness.memory import MemoryWithoutTenancy, scope_namespace_of, shared_namespace_of
from rag_wright.harness.models import agent_chat_model
from rag_wright.harness.preflight import (
    STUB_ORCHESTRATOR_REPLY,
    WhatWillRun,
    only_the_chat_manager,
    stub_orchestrator,
    what_will_run,
)
from rag_wright.harness.recording import RecordTheContext, compare, describe, every_call, spend, transcript
from rag_wright.harness.rules import (
    Rule,
    blocked_without_saying_what_it_needs,
    dispatched_more_than_once,
    guessed_identifiers,
    reached_for_the_filesystem,
    rule_named,
    wrote_a_program_for_a_single_call,
)
from rag_wright.harness.runs import (
    RunOutcome,
    RunResult,
    outcome_of,
    read_run,
    resume_run,
    start_run,
    stream_run,
    thread_for,
)
from rag_wright.harness.scope import RunScope, scope_namespace, shared_namespace, thread_key, validate_scope
from rag_wright.harness.seen import run_key
from rag_wright.harness.skills import ORCHESTRATING_PATH
from rag_wright.harness.tiers import (
    build_orchestrator,
    chat_manager_tools,
    direct_names,
    orchestrator_direct,
    worker_operations,
)
from rag_wright.harness.tracing import harness_tracing_on
from rag_wright.harness.withhold import withhold
from rag_wright.harness.workers import CANNOT_ASK, DO_WHAT_WAS_ASKED, WorkerReport, build_worker, worker_spec

__all__ = [
    # what a product declares
    "AgentDefinition", "AgentPrompts", "ToolSurface", "RunScope", "validate_scope",
    # running an agent: the one door
    "start_run", "resume_run", "stream_run", "read_run", "RunResult", "RunOutcome", "outcome_of", "thread_for",
    "build_chat_agent", "build_orchestrator", "agent_chat_model", "accounted_run", "run_key", "harness_tracing_on",
    # namespaces the product's stores are keyed by
    "scope_namespace", "shared_namespace", "thread_key", "MemoryWithoutTenancy", "scope_namespace_of",
    "shared_namespace_of",
    # the tier split, derived from the definition: for a product's guards on its own composition
    "chat_manager_tools", "direct_names", "orchestrator_direct", "worker_operations",
    "withhold", "DISPATCHES_PER_RUN", "bridged_tools", "js_name",
    # workers and their reports
    "worker_spec", "WorkerReport", "CANNOT_ASK", "DO_WHAT_WAS_ASKED", "build_worker",
    # human in the loop and the run's event stream
    "clarification_tool", "ConfirmTheView", "RunEvent", "translate", "what_was_finished",
    # failures and their bounds
    "ModelTooSlow", "ModelAttemptTooSlow", "ModelTurnTooSlow", "error_middleware",
    # the rules a run is graded against, and the decisions it records
    "Rule", "dispatched_more_than_once", "reached_for_the_filesystem", "wrote_a_program_for_a_single_call",
    "guessed_identifiers", "blocked_without_saying_what_it_needs", "rule_named", "DecisionPoint", "ANY_WORKER",
    "WhatTheRunDecided", "RecordTheDecisions",
    "ORCHESTRATING_PATH",
    # what a run did, recorded; and grading it against the rules
    "RecordTheContext", "every_call", "spend", "compare", "transcript", "describe",
    "GradeThisTier", "grading_middleware", "rule_tools", "Verdict", "GRADER_ERROR",
    # an eval's instruments: faults on demand, and what a run will build
    "Fault", "InjectFaults", "WhatWillRun", "what_will_run", "only_the_chat_manager", "stub_orchestrator",
    "STUB_ORCHESTRATOR_REPLY",
]
