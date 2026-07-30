"""LG-0: the reusable pattern + primitives for hardened subgraph capabilities (LangGraph).

A `subgraph` capability is a compiled `StateGraph` invoked as a subagent under a contract. Every RAG_Wright
subgraph is built the same way so hardening is uniform and hermetic-testable:

  - **Dependency-injected model/store** (no global clients) -> tests inject fakes, no live LLM/DB.
  - A **RetryPolicy** (`DEFAULT_RETRY`) on LLM / IO nodes for transient failures (network, rate limit, 5xx).
    Configure `retry_on` per node for that node's real transient exceptions -- LangGraph's default retry_on
    does NOT retry `ValueError`/`OSError`/etc., so a provider error mapped to one of those needs an explicit
    `retry_on` (or a dedicated transient exception).
  - A conditional **ESCALATION** edge (cheap model -> stronger model, ADR-0028 Flash->Pro), bounded by an
    attempt counter in state -- this is a routing decision, distinct from a retry of the same node.
  - An optional `interrupt()`-based **HUMAN GATE** for low-confidence / high-stakes items.
  - A **DEAD-LETTER** terminal: a bad item is dropped with a reason (never raised), so one item never kills a
    batch (the "17% dead-lettered" lesson).

`typed_clause_extraction` (LG-1) is the reference implementation; other subgraphs follow this shape.
Related grounding: the LangChain docs MCP (`mcp__docs-langchain__*`) + the framework index (langgraph AST).
"""

from __future__ import annotations

from typing import Any

from langgraph.types import RetryPolicy

# The shared retry for LLM / IO nodes. Backoff on transient failures; bounded attempts. Note LangGraph's
# default `retry_on` already skips programmer errors (TypeError/ValueError/ImportError/...) and only retries
# 5xx for requests/httpx -- so a node whose transient error surfaces as one of those must pass its own
# `retry_on` when it adds the node (see the reference subgraph / LG-1).
DEFAULT_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0)


def dead_letter(reason: str, **fields: Any) -> dict:
    """A terminal dead-letter record: the item is dropped with a `reason` (not raised) so the batch survives.

    Put it on the subgraph state's `dead_letter` key; the caller filters out items that carry one. Extra
    `fields` capture context (the offending id, the exception text, the node) for diagnosis.
    """
    return {"reason": reason, **fields}
