"""What a product declares to build an agent on the substrate -- and nothing the substrate can derive (PS-32).

These are the types a product FILLS, so they belong to the public tier: a product imports them from
`rag_wright.harness` rather than copying another product's. The substrate reads a definition through these fields
only; everything else about a tier split follows from them:

- `surface` -- which operations an agent may call, and how each binds (`ToolSurface`)
- `planning` -- which of THOSE need a planning tier, with the reason each does
- `workers` -- the roster the orchestrator may dispatch

The direct surface is `exposed - planning`, everything exposed is orchestrator-reachable, and the worker names come
off the roster. Handing the substrate pre-computed sets instead would let a product pass three sets that disagree,
so its chat-manager could directly call an operation that needs planning, silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolSurface(Protocol):
    """What a product offers an agent run, and the two things the substrate asks about it."""

    def build(self, context: Any, resources: Any, *, only: Any = None) -> list[Any]:
        """This run's bound tools, or just the named ones. Bound to the run's own scope, so a tool never takes a
        scope argument the model could name. `only` filters the registry (not a built list), so the chat-manager
        tier gets the same bound objects the orchestrator would."""
        ...

    @property
    def exposed_names(self) -> set[str]:
        """The operations the model may call. The substrate decides from it which tier plans; the product decides
        what is on it."""
        ...


@dataclass(frozen=True, slots=True)
class AgentPrompts:
    """Every word a product says to its model: the tier prompts, the memory line and the authored skills.

    **Frozen**: a prompt a middleware could edit mid-run is one where two turns were given different instructions.
    """

    #: The chat-manager's system prompt: the tier a person talks to.
    chat: str
    #: The orchestrator's, with the product's worker roster already interpolated into it.
    orchestrator: str
    #: One line of guidance about the agent's own memory, shown above whatever is in it.
    memory: str
    #: `{published path: text}` -- the procedures a tier may read when they apply.
    skills: dict[str, str]
    #: The paragraph that introduces the skills above.
    skills_preamble: str
    #: The signpost a wrong filesystem call is answered with by default (it names the product's own tools).
    not_a_filesystem: str
    #: The signpost for a filesystem search plainly hunting the subject documents (a path or pattern ending in
    #: one of `subject_file_endings`): it says where those documents ARE, rather than where they are not.
    looking_for_subjects: str
    #: File extensions that mean "this model is hunting the subject documents it was shown", not notes.
    subject_file_endings: tuple[str, ...]
    #: What a worker is told about its own filesystem.
    worker_filesystem_note: str
    #: How the orchestrator is described to the chat-manager: the `description` of its compiled subagent, which
    #: is what the chat-manager reads when deciding whether to hand a request down.
    orchestrator_description: str
    #: What the model is shown as the SOURCE of the skills: the product's name.
    label: str


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """One product's agent, as the substrate needs to see it.

    **Frozen**: a definition a middleware could edit mid-run is one where the tier split differs between two calls
    in the same run.
    """

    surface: ToolSurface
    #: Operation name -> why it needs planning. The reason is kept: a classification without one cannot be revisited.
    planning: dict[str, str]
    #: The worker factories the orchestrator may dispatch.
    workers: tuple[Any, ...]
    #: The type a run's context has, passed to the agent as `context_schema`; it must satisfy `RunScope`. The
    #: product's context stays its own: typed ids, its own construction rule, whatever else it wants.
    context_schema: Any
    #: Which exceptions the retry middleware may re-attempt. **Named, never `Exception`** -- the library's default
    #: would retry a scope violation, which is re-attempting a leak with backoff.
    retry_on: tuple[type[BaseException], ...]
    #: `(error) -> str | None`: what the model is told about a failure, or `None` to let it surface.
    message_for: Any
    #: `(settings) -> str`: the ENGINE model id this product's agent reasons with (a registered profile's key).
    #: A model id is configuration: adopting a better model is a new profile and a changed setting.
    model_id_for: Any
    #: Every word this product says to its model (`AgentPrompts`).
    prompts: AgentPrompts
    #: Tools whose answer is the same twice in one run, so a repeat can be served from the record.
    repeatable: frozenset[str]
    #: `{tool: argument}` -- which argument carries a QUESTION, so two phrasings of one ask are one ask.
    question_argument: dict[str, str]
    #: Tools that FRAME a request rather than answer it, for the framing decision point.
    framings: tuple[str, ...]
    #: `(record, tool, content) -> content | None`, or `None`: the product's chance to amend a result in the light
    #: of what this run already found. `None` amends nothing.
    carry_forward: Any
    #: The rules this product holds its own runs to. The grading machinery is the substrate's; the rubric is not.
    rules: tuple[Any, ...]
    #: The decisions a run of this product takes; the ledger reads each point's option space from here.
    decision_points: tuple[Any, ...]
    #: `(tool_name, parsed_result) -> {chose, confidence, inputs} | None`: how this product reads its own framing
    #: tool's reply.
    read_framing: Any
