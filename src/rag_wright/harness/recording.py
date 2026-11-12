"""Recording exactly what an agent was given, turn by turn, and what it did about it.

**The run's evidence, in the one shape the harness reads.** The rule detectors (`rules.broken`) and the grader's
evidence tool (`grading.rule_tools`) read a recording; this middleware writes it. They were written against a shape
only a product's own eval code produced, so a second product could not use either without reverse-engineering it
(PS-48e). Writer and readers now live together and are tested against each other.

**So a tier's context can be COMPARED between isolation and orchestration.** A worker under a parent receives a brief
written by the orchestrator, not the person's question; attached to the same spec in both modes, the two recordings
are the same object and can be diffed directly.

**Every turn is captured whole, with every tool call's arguments.** A count of calls is a symptom, not a diagnosis:
what distinguishes a worker rephrasing one question twenty-three times from one asking twenty-three different
questions is the argument values.

**Local, beside the trace.** The trace carries the cost and the same prompts at the `verbose` level; this carries a
deterministic, diffable snapshot that needs no network round trip.

**It records, and never changes anything.** `wrap_model_call` passes the request straight through: a recorder that
altered a prompt would be measuring itself.

The shape, as the detectors read it::

    {"system_prompt": str, "system_prompt_chars": int, "opening_messages": [message], "tools_offered": [str],
     "dispatches": [brief, ...],
     "turns": [{"n": int, "context_chars": int, "new": [message], "messages": [message],
                "tools_offered": [str] | None, "said": [message], "tokens": {"input": int, "output": int}}]}

where a message is `{"role", "text", "tool_calls"?: [{"name", "args"}], "name"?, "clipped_from"?}`.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from rag_wright.harness.models import _as_recorded

#: Longest message text kept verbatim. A whole document in a tool result would make the record unreadable without
#: telling a reader anything the first few thousand characters do not.
KEEP_CHARS = 6000


def _message(message: Any) -> dict[str, Any]:
    """One message, shaped by the harness's own trace recorder and clipped for readability.

    **`_as_recorded` rather than a second shaping function.** The trace and the recording must agree about what a
    message looked like; two implementations of "flatten a message" drift, which is how a tool call's arguments
    once came to be in neither. A clipped message says so, because one that does not is a lie about what the model
    saw.
    """
    recorded = _as_recorded(message)
    full = recorded.get("text", "")
    if len(full) > KEEP_CHARS:
        recorded["text"] = full[:KEEP_CHARS]
        recorded["clipped_from"] = len(full)
    return recorded


class RecordTheContext(AgentMiddleware):
    """Capture the system prompt and every message an agent sees, and what it said, on every turn, into `sink`."""

    def __init__(self, sink: dict[str, Any], label: str) -> None:
        super().__init__()
        self._sink = sink
        self._label = label

    @property
    def name(self) -> str:
        """Distinct per label: `create_agent` refuses two middlewares sharing a name."""
        return f"RecordTheContext({self._label})"

    def wrap_model_call(self, request, handler):
        turn = self._capture(request)
        response = handler(request)
        self._reply(turn, response)
        return response

    async def awrap_model_call(self, request, handler):
        turn = self._capture(request)
        response = await handler(request)
        self._reply(turn, response)
        return response

    def _reply(self, turn: dict[str, Any], response: Any) -> None:
        """What the model said in answer to this turn, recorded ON this turn.

        **Reading the calls out of the NEXT turn's history is off by one**: a call made on turn 1 first appears in
        the history on turn 2. `ModelResponse.result` is the reply to the request just captured, so recording it
        here needs no arithmetic and cannot drift. Tokens are recorded per turn and locally, so a tier run in
        isolation (with no tracing attached) can be compared with the same tier orchestrated.
        """
        replies = getattr(response, "result", None) or []
        turn["said"] = [_message(m) for m in replies]
        used = [getattr(m, "usage_metadata", None) for m in replies]
        turn["tokens"] = {
            "input": sum((u or {}).get("input_tokens", 0) for u in used),
            "output": sum((u or {}).get("output_tokens", 0) for u in used),
        }

    def _capture(self, request) -> dict[str, Any]:
        turns = self._sink.setdefault("turns", [])
        system = _as_recorded(getattr(request, "system_message", None) or "")["text"]
        messages = [_message(m) for m in (getattr(request, "messages", None) or [])]
        offered = sorted(getattr(t, "name", str(t)) for t in (getattr(request, "tools", None) or []))

        if not turns:
            # **The first turn is the whole of what the agent was handed before it did anything** -- the system
            # prompt plus the task -- and it is what an isolated-versus-orchestrated diff compares.
            self._sink["system_prompt"] = system
            self._sink["system_prompt_chars"] = len(system)
            self._sink["opening_messages"] = messages
            self._sink["tools_offered"] = offered

        # **Every DISPATCH, not just the first.** A prompted orchestrator dispatches with a `task` tool call, visible
        # in its transcript; a coded one calls `task()` inside a sandbox, where no tool call is emitted -- so
        # counting dispatches from the transcript sees none, and a run that fanned a worker out per document once
        # scored a perfect "dispatched once each". A dispatch starts the subagent afresh, so its first turn carries
        # exactly one human message: the brief. That holds in both arms, so it is the one definition used.
        if len(messages) == 1 and messages[0]["role"] == "human":
            self._sink.setdefault("dispatches", []).append(messages[0]["text"])

        turn = {
            "n": len(turns) + 1,
            "context_chars": len(system) + sum(len(m["text"]) for m in messages),
            # **What ARRIVED since the last turn**: on turn 14 of 23 the interesting thing is the one tool result
            # that came back, not the thirteen turns of history above it.
            "new": messages[len(turns[-1]["messages"]):] if turns else messages,
            "messages": messages,
            # Per turn, because a middleware can withhold a tool on some turns and not others.
            "tools_offered": offered if offered != self._sink.get("tools_offered") else None,
            "said": [],
        }
        turns.append(turn)
        return turn


def describe(sink: dict[str, Any]) -> str:
    """A one-screen summary of what an agent was given."""
    opening = sink.get("opening_messages") or []
    task = next((m["text"] for m in opening if m["role"] == "human"), "")
    return (f"system={sink.get('system_prompt_chars', 0)} chars | "
            f"opening messages={len(opening)} | turns={len(sink.get('turns') or [])} | "
            f"tools={len(sink.get('tools_offered') or [])}\n"
            f"  task given: {task[:300]!r}")


def every_call(sink: dict[str, Any]) -> list[dict[str, Any]]:
    """Every tool call the agent made, in order, **with its arguments** and the turn it was made on."""
    calls: list[dict[str, Any]] = []
    for turn in sink.get("turns") or []:
        for message in turn.get("said") or []:
            for call in message.get("tool_calls") or []:
                calls.append({"turn": turn["n"], "tool": call["name"], "args": call["args"]})
    return calls


def spend(sink: dict[str, Any]) -> dict[str, int]:
    """What this agent cost: model calls, tool calls and tokens -- the unit of an isolated-versus-orchestrated
    comparison. A model that reports no usage counts as zero, never as a crash."""
    turns = sink.get("turns") or []
    return {
        "model_calls": len(turns),
        "tool_calls": len(every_call(sink)),
        "input_tokens": sum((t.get("tokens") or {}).get("input", 0) for t in turns),
        "output_tokens": sum((t.get("tokens") or {}).get("output", 0) for t in turns),
        "context_chars_peak": max((t.get("context_chars", 0) for t in turns), default=0),
    }


def compare(isolated: dict[str, Any], orchestrated: dict[str, Any]) -> str:
    """One table: the same tier alone and under a parent, **as ratios as well as totals**, because "23 calls" only
    means something beside the 3 the same tier took alone."""
    alone, under = spend(isolated), spend(orchestrated)
    lines = [f"  {'':20} {'alone':>10} {'orchestrated':>13} {'ratio':>8}"]
    for field in ("model_calls", "tool_calls", "input_tokens", "output_tokens", "context_chars_peak"):
        a, u = alone[field], under[field]
        ratio = f"{u / a:.2f}x" if a else ("--" if not u else "inf")
        lines.append(f"  {field:20} {a:>10} {u:>13} {ratio:>8}")
    return "\n".join(lines)


def transcript(sink: dict[str, Any], *, width: int = 200) -> str:
    """The run as a person would read it at the moment something has gone wrong: what arrived each turn, and what
    the agent did about it."""
    lines = [f"system prompt: {sink.get('system_prompt_chars', 0)} chars",
             f"tools offered: {sink.get('tools_offered')}"]
    for turn in sink.get("turns") or []:
        lines.append(f"--- turn {turn['n']} ({turn['context_chars']} chars of context) ---")
        for message in turn.get("new") or []:
            label = message["role"] + (f"/{message['name']}" if message.get("name") else "")
            if message.get("text"):
                lines.append(f"  in   {label}: {message['text'][:width]!r}")
        for message in turn.get("said") or []:
            if message.get("text"):
                lines.append(f"  out  ai: {message['text'][:width]!r}")
            for call in message.get("tool_calls") or []:
                lines.append(f"  out  ai -> {call['name']}({call['args']})")
        if turn.get("tools_offered"):
            lines.append(f"  [tools changed this turn: {turn['tools_offered']}]")
    return "\n".join(lines)
