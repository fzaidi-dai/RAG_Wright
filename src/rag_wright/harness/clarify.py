"""Asking the user mid-run (T-5a.9, AR-15, AR-16).

**One rule, and it comes from a verified fact rather than a principle.** `interrupt()` suspends the run;
resuming **replays the node from the start**, so everything before the interrupt runs twice. Pinned in
`test_harness_grounding.py` as `["before", "before", "after"]`, on both the fake and live tiers.

So a tool that spends money, writes a row, or sends anything before its question does it **once on the way
in and again on the way back** — silently, because the second pass looks identical to the first. The rule
that follows is not stylistic: **gather the arguments, call `interrupt()`, act only afterwards.**

`ask_the_user` takes that to its conclusion: it does nothing but ask. There is no "afterwards" to get
wrong, and `test_harness_clarify.py` reads the function body to keep it that way.

**Why a tool rather than a middleware.** `interrupt_on` gates a tool the model already chose to call —
right for "may I do this?", wrong for "which of these did you mean?". A clarification is something the
model decides it needs, so it has to be something the model can reach for.

**Not an operation, and therefore not in the manifest.** The manifest is derived from
`rulewright.operations` in exact equality (`AR-1`); a clarification touches no product state and has no
operation behind it. It joins the agent's tool surface beside the manifest, and the tenancy guards cover
the whole surface rather than the manifest alone — which is what `AR-5` actually requires.

**`AR-16` is deliberately not here.** Batching clarifications belongs to the orchestrator: a worker that
would ask instead returns "I need X" and the orchestrator asks once rather than five times. That needs
tiers, which is T-5a.11.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool


def clarification_tool() -> BaseTool:
    """A tool whose entire body is a question.

    Returned from a factory for symmetry with the manifest's bindings, though it closes over nothing: a
    clarification has no tenancy to bind, because it touches no matter's data.
    """

    @tool
    def ask_the_user(question: str) -> Any:
        """Ask the person a question and wait for their answer before going on.

        Use this when you genuinely cannot proceed without knowing something only they can tell you —
        which of two documents they meant, whether to spend a long search on a broad question, which
        reading of an ambiguous request is right.

        **Do not use it to check in, to confirm something you already know, or to narrate progress.** Each
        use stops the run and waits for a person, so a question they could not have expected to answer is
        worse than no question.

        Ask one thing at a time, in the user's own vocabulary, and say why you need it.
        """
        # **The entire body, and it must stay that way.** Resume replays this function from the top, so
        # anything above this line would happen twice -- a spent search, a written row, a sent message.
        # `test_harness_clarify.py` reads this body and fails if a statement appears before the interrupt.
        return interrupt({"question": question})

    return ask_the_user


def interrupt(payload: dict[str, Any]) -> Any:
    """`langgraph.types.interrupt`, re-exported so the guard has one name to look for.

    Imported lazily rather than at module scope because the guard reads the tool's source for a call to
    `interrupt` — and an aliased or attribute-qualified import would be a second spelling to chase.
    """
    from langgraph.types import interrupt as _interrupt

    return _interrupt(payload)
