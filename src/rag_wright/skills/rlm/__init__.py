"""RLM skill package — the divide-and-conquer method (FR-C.10).

Exposes the authored RLM method: the recursive workflow that holds the working set in the
interpreter, dispatches sub-agents per level (a fresh ``rlm_decomposer`` each) and per leaf
(``rlm_slice_worker``), then combines results. Two applying capabilities build on this skill:
RLM chunking (FR-I.1, ingestion) and RLM synthesis (FR-Q.5, query).
"""

from rag_wright.skills.rlm.agent import (
    GRANTED_SUBAGENTS,
    RLM_DECOMPOSER,
    RLM_SLICE_WORKER,
    RLM_WORKFLOW_JS,
    build_rlm_agent,
    decomposer_config,
    method_prompt,
    rlm_interpreter_session,
    slice_worker_config,
)

__all__ = [
    "GRANTED_SUBAGENTS",
    "RLM_DECOMPOSER",
    "RLM_SLICE_WORKER",
    "RLM_WORKFLOW_JS",
    "build_rlm_agent",
    "decomposer_config",
    "method_prompt",
    "rlm_interpreter_session",
    "slice_worker_config",
]
