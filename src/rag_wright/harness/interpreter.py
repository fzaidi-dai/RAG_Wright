"""Orchestrating in code.

**Two different things are called "the interpreter", and only one of them works here.**

`execute` comes from `FilesystemMiddleware` and runs a **shell command in a sandbox backend**. Ours is
`StateBackend`, which is not a `SandboxBackendProtocol`, so every call returns *"Error: Execution not
available. This agent's backend does not support command execution."* Measured, not assumed — and an
advertised tool that can only fail is a dead end (the same lesson as the documents-are-not-files signpost),
so it is withheld from both tiers.

`eval` comes from `langchain_quickjs`'s `CodeInterpreterMiddleware` and runs **JavaScript in-process**,
memory-limited and timed out, with a host bridge (`ptc`) that lets a program call our tools. That is what
orchestrating in code means here: no new deployment surface, no shell, and the dependency
(`langchain-quickjs`) is already declared.

**`ask_the_user` is never bridged, and this is the sharpest rule in the module.** `interrupt()` suspends
the run and resume **replays the node from the start** (verified as `["before", "before",
"after"]`). A question raised from inside a loop would re-run **the whole program** on the way back —
every tool call it had already made, again, with nothing on screen to say so. A worker in that position
reports `status="blocked"` instead; a program has no such affordance, so it must not be able to
ask at all.

**Whether this is ADOPTED is not settled here.** The task is conditional: a fan-out written in code has to
beat the prompted equivalent on a measured case, or it is not used. This module makes the
measurement possible and takes the dead tool away.
"""

from __future__ import annotations

from typing import Any

#: How many host-tool calls one program may make.
#:
#: **A decision rather than the library's 256**, and it is load-bearing: `ToolCallLimitMiddleware` counts
#: the calls the MODEL makes and cannot see a call made from inside the sandbox, so a loop over a corpus is
#: exactly the shape that spends money without the run's own cap noticing. Forty is the same order as
#: `TOOL_CALLS_PER_RUN`, which is the budget a prompted fan-out would have had.
PTC_BUDGET = 40

#: How long one program may run, in seconds. The library's default is 5.0; a fan-out that reads forty
#: documents is doing real work, so this is raised deliberately rather than by accident.
PROGRAM_TIMEOUT = 60.0


#: Tools that must never be bridged, and why. See the module docstring: a resume replays the whole program.
NEVER_BRIDGED: dict[str, str] = {
    "ask_the_user": (
        "It interrupts. Resume replays the node from the start, so a question raised from inside "
        "a loop would re-run the whole program, repeating every call it had already made."
    ),
}


def bridged_tools(context: Any, resources: Any, *, surface: Any) -> list:
    """The tools a program in the sandbox may call.

    **`surface` is handed in rather than imported.** The tool registry is the PRODUCT's -- its named
    operations and its governance over them. A generic substrate cannot know those names, so it asks
    whatever it was given for the run's bound tools.

    **The run's own bindings**, so tenancy stays out of the model's reach inside the sandbox too: a bridged
    tool that took a workspace argument would let a JS program name another scope.

    **The agent's WHOLE surface minus what interrupts**, which is what this docstring always claimed — the
    first version bridged `build_manifest` instead, a set that differs for an unrelated reason. The gap was
    `read_the_question`: on the orchestrator's tool surface, absent from the sandbox. A model that had been
    given the camelCase convention generalised it correctly, wrote `tools.readTheQuestion(...)`, and got
    `TypeError: not a function` — so the coded arm of a code-versus-prompt comparison failed for a reason
    that had nothing to do with whether code beats prompting.

    **A surface the model can see but the sandbox cannot is a trap**, and the same shape as the
    documents-are-not-files collision: our
    own inconsistency, discovered by a model behaving reasonably.
    """
    return [tool for tool in surface.build(context, resources) if tool.name not in NEVER_BRIDGED]


def code_interpreter(context: Any, resources: Any, *, surface: Any) -> Any:
    """The orchestrator's interpreter, bridged to this run's tools."""
    from langchain_quickjs import CodeInterpreterMiddleware

    return CodeInterpreterMiddleware(
        ptc=bridged_tools(context, resources, surface=surface),
        max_ptc_calls=PTC_BUDGET,
        timeout=PROGRAM_TIMEOUT,
    )


def js_name(tool_name: str) -> str:
    """The name a bridged tool answers to inside the sandbox.

    **The bridge camelCases our snake_case names**: `sweep_scope` is `tools.sweepScope`. A model that
    knows our tool names — every model on this tier does, they are in its schema — writes
    `tools.sweep_scope(...)` and gets `TypeError: not a function`. The same namespace collision as
    the documents-are-not-files one, one layer down, and the reason anything that tells the orchestrator how
    to write a program must use these names.

    A test drives the real sandbox and compares, so this cannot drift from what the bridge actually does.
    """
    head, *rest = tool_name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)
