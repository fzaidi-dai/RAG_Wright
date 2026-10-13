"""What a run tells the client while it runs (T-5a.4c-ii, AR-17, AR-18).

**Streamed from FastAPI, not from an Agent Server.** `astream(subgraphs=True, stream_mode=[…])` yields
`(namespace, mode, chunk)` — verified against the installed version rather than assumed, because the tuple
is a 2-tuple with one stream mode and a 3-tuple with several, and reading the wrong arity produces events
that look plausible and carry the wrong field. The namespace is `()` for the main agent and
`("tools:<call-id>", …)` inside a subagent, which is what lets the client route without a second service
or a second auth story (`NFR-7`).

**The contract is ours, not langgraph's**, and that is deliberate. Forwarding raw chunks would make the
client depend on node names — `PatchToolCallsMiddleware.before_agent` is real output — and every middleware
change would become a front-end change. So this translates: a small, named set of events the surface can
be written against, with the namespace carried so `AR-18`'s subagent cards can index on it.

**What is deliberately NOT in an event.** No tenancy: `AR-5` keeps it out of everything the model can
reach, and an SSE frame is more exposed than a prompt. No raw tool arguments either, for now — a tool call
naming a document handle is fine, but arguments are model-authored text and the surface has no need to
render them until `AR-18`'s cards exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The main agent. A subagent's namespace is non-empty, e.g. `("tools:call_abc",)`.
MAIN = ""


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One thing worth telling the client about.

    `name` is the event type the client routes on; `namespace` says which agent produced it, joined with
    `|` so it survives an SSE field without needing JSON.
    """

    name: str
    data: dict[str, Any]
    namespace: str = MAIN


def _namespace(raw: Any) -> str:
    """`()` for the main agent, otherwise the subagent path."""
    return "|".join(str(part) for part in raw) if raw else MAIN


def translate(namespace: Any, mode: str, chunk: Any) -> list[RunEvent]:
    """Turn one streamed item into zero or more events for the client.

    **Zero is a normal answer.** Most of what a graph emits is bookkeeping — middleware hooks, state
    patches — and forwarding it would tie the surface to node names that change whenever the middleware
    stack does.
    """
    where = _namespace(namespace)

    if mode == "messages":
        return _message_events(chunk, where)
    if mode == "updates":
        return _update_events(chunk, where)
    return []


def _message_events(chunk: Any, where: str) -> list[RunEvent]:
    """Token-level output, which is the only thing that makes an answer feel alive.

    `messages` mode yields `(message_chunk, metadata)`. Only the text is forwarded: a chunk also carries
    ids and response metadata that no surface needs and that would grow every frame.
    """
    message = chunk[0] if isinstance(chunk, tuple) and chunk else chunk
    text = getattr(message, "content", None)

    if not text or not isinstance(text, str):
        return []
    return [RunEvent("token", {"text": text}, where)]


def _update_events(chunk: Any, where: str) -> list[RunEvent]:
    """Node completions, of which exactly one kind matters to a user: a tool ran.

    **`AR-18`'s two progress surfaces start here.** A tool call is the unit a card is drawn for, and the
    namespace is the index those cards are kept under — which is why it travels with the event rather than
    being inferred from arrival order, since two subagents interleave.
    """
    if not isinstance(chunk, dict):
        return []

    events: list[RunEvent] = []
    for node, payload in chunk.items():
        if node != "tools" or not isinstance(payload, dict):
            continue
        for message in payload.get("messages", []) or []:
            name = getattr(message, "name", None)
            if name:
                events.append(RunEvent("tool", {"tool": name}, where))
    return events


#: Which outcomes owe the user an explanation. **`COMPLETE` and `WAITING_ON_A_PERSON` are deliberately
#: absent**: a run that answered needs no caveat, and one holding a question has not failed -- telling the
#: user work was lost while their answer is outstanding would be false.
NEEDS_EXPLAINING = ("partial", "nothing_came_back")


def what_was_finished(result: Any) -> list[RunEvent]:
    """What to tell the client about a run that did not finish (T-5a.15b).

    **The route already stated the principle and did not honour it for failures:** *"a stream that stops is
    not a stream that finished. Without a terminal event a client cannot tell a completed answer from a
    dropped connection."* On a cut-off run the stream simply stopped -- no partial, no explanation, not even
    the terminating frame -- so it looked exactly like a dropped connection.

    **There is nothing to store, only something to say.** Three reproduced failures showed that when our
    whole-turn clock fires, and when a caller's own ceiling fires, the finished work is still in the
    checkpoint. This reads it back out.

    **And a run that produced NOTHING says so**, rather than being dressed as a partial answer. That
    distinction was measured, not designed: when the retries are exhausted the model never answered at all,
    and reporting an empty gap as work is worse than reporting a failure.

    **No tenancy, ever** -- the module's rule, and an SSE frame is more exposed than a prompt. Only tool
    names and the results they returned.
    """
    outcome = getattr(result, "outcome", None)
    if outcome is None or str(outcome) not in NEEDS_EXPLAINING:
        return []

    finished = _results_in(getattr(result, "state", None) or {})
    return [RunEvent(name="incomplete", data={
        "outcome": outcome,
        "finished": finished,
        "missing": ("the answer was never composed from the work below"
                    if finished else "nothing came back before the run was cut off"),
    })]


def _results_in(state: dict[str, Any]) -> list[dict[str, str]]:
    """Every tool result the run produced, as `{tool, result}`.

    **A `ToolMessage` is the evidence that work finished**, which is exactly what the reproduced failures
    differed on -- and it is read from the stored state rather than inferred from timing or from an
    exception type, because that type was itself wrong until T-5a.13c.
    """
    return [
        {"tool": str(getattr(message, "name", "") or ""), "result": str(getattr(message, "content", ""))}
        for message in (state.get("messages") or [])
        if type(message).__name__ == "ToolMessage"
    ]
