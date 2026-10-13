"""What this run has already asked, and what came back (T-5a.12h, AR-2, ANFR-6).

**Three things in this harness had started remembering the run separately**: a bespoke closure inside
`sweep_matter` so a later sweep could not unfind an earlier one, a signpost that remembered nothing and so
said the same sentence to every wrong turn, and the eval's recorder. Each new symptom would have added a
fourth. This is the one record, and the others read it.

**It removes work rather than commenting on it.** Across every run we have recorded — 194 tool calls —
**25 were byte-identical repeats**: same tool, same arguments, a second real retrieval for an answer
already in the transcript. `ask_contract_question` was called again 34 times beyond its first use of each
document. An identical call is answered from the record, so the operation never runs and the payload never
re-enters the context.

**Opt-IN, never opt-out.** Only tools whose answer cannot change during one run are eligible. A job's
status changes while the run watches it, and `ask_the_user` must reach a person every time — so a list of
what is safe is the honest shape, and a tool added tomorrow is excluded until somebody says otherwise.

**Scoped to one RUN, and getting the scope right took two attempts.** A worker's tools and middleware are
built once, when the orchestrator is compiled -- not per dispatch -- so a plain closure is shared by every
dispatch and would serve one brief's answer to another. The first fix went the other way and keyed by
DISPATCH, which turned out to be too narrow: live, a worker was re-dispatched and asked the same four
documents the same question in different words, and nothing matched because the two halves sat either
side of a dispatch boundary.

**The dispatch boundary was guarding nothing.** What prevents a wrong answer is the SUBJECT check -- same
document, same question -- not which brief did the asking, and a document does not change within a run. So
the key is `thread_id`, which `thread_for` builds from the workspace and the run id: shared across every
dispatch of one run, and never across runs or tenants.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)


#: How much of an earlier answer to quote back when refusing a repeat. Enough to be usable if the original
#: has been summarised away, small enough that repeating is never cheaper than remembering.
DIGEST = 400


#: Cosine at or above which two phrasings are treated as the SAME question and the earlier answer is
#: served instead of a second retrieval.
#:
#: **Measured on the real pairs from a live run**, not chosen: three reads of one document phrased three
#: ways scored 0.652-0.885 against each other, while genuinely different asks about the same document
#: (four different facts asked of it) scored 0.356-0.516.
#:
#: **Set high deliberately.** The pair at 0.652 is *"does this contain a cap?"* against *"quote section 7
#: verbatim"* -- related, and NOT the same question: one asks existence, the other asks for text. Serving
#: either answer for the other would be a wrong answer, which is worse than a repeated call. So only a
#: near-identical phrasing substitutes; everything in between gets told what has already been asked.
THE_SAME_QUESTION = 0.85

#: Between this and `THE_SAME_QUESTION`, two questions are about the same thing without being the same
#: ask. The call RUNS -- substituting would risk a wrong answer -- but the result carries what has already
#: been asked of this subject and what it returned.
#:
#: **This band is where a spiral lives.** Live, one document was read three times with phrasings scoring
#: 0.65-0.88 against each other: the third would not have been asked if the second's result had said what
#: the first had already established. Below 0.6 the pairs in that run were unrelated (different asks about
#: the same contract topped out at 0.516), so a note there would be noise.
WORTH_MENTIONING = 0.6


@dataclass
class Call:
    """One tool call this run made, and what it returned."""

    tool: str
    args: dict[str, Any]
    result: Any
    turn: int

    @property
    def key(self) -> str:
        return f"{self.tool}({json.dumps(self.args, sort_keys=True, default=str)})"

    @property
    def digest(self) -> str:
        """The answer in brief. Prefers a result's own headline over a truncation of its payload."""
        if isinstance(self.result, dict):
            for field_name in ("result", "detail", "answer", "note"):
                said = self.result.get(field_name)
                if isinstance(said, str) and said.strip():
                    return said[:DIGEST]
        return str(self.result)[:DIGEST]


@dataclass
class RunRecord:
    """Every tool call one run has made, in order, across all of its dispatches."""

    calls: list[Call] = field(default_factory=list)
    #: The PRODUCT's tool facts (T-7.58): which tools answer the same twice in one run, and which
    #: argument of each carries a question. **Empty means "know nothing"**, which disables repeat
    #: suppression rather than guessing -- the substrate had this product's twelve tool names hardcoded.
    repeatable: frozenset[str] = frozenset()
    question_argument: dict[str, str] = field(default_factory=dict)

    def remember(self, tool: str, args: dict[str, Any], result: Any) -> Call:
        call = Call(tool=tool, args=dict(args or {}), result=result, turn=len(self.calls) + 1)
        self.calls.append(call)
        return call

    def identical(self, tool: str, args: dict[str, Any]) -> Call | None:
        """The earlier call with these exact arguments, if this run made one."""
        wanted = Call(tool=tool, args=dict(args or {}), result=None, turn=0).key
        return next((call for call in self.calls if call.key == wanted), None)

    def by_tool(self, *tools: str) -> list[Call]:
        return [call for call in self.calls if call.tool in tools]

    def last_answered_nothing(self) -> Call | None:
        """The most recent product tool that came back empty.

        **This is the second cause of a filesystem wrong turn**, and the one no gate could see before:
        `grep('Acme')` came straight after `trace_party_exposure` reported `resolved: False,
        contracts: []`. The model was not confused about what a filesystem is; it was looking for a second
        opinion after the first answer was empty.
        """
        for call in reversed(self.calls):
            if call.tool in self.repeatable and answered_nothing(call.result):
                return call
        return None


def answered_nothing(result: Any) -> bool:
    """Whether a tool result is empty in the sense a model would notice.

    **Declared in ONE place rather than inferred per caller.** Each product tool says "nothing" in its own
    shape -- an unresolved party, a sweep with no spans, an empty policy list -- and a second opinion about
    emptiness scattered across callers is how two of them come to disagree.
    """
    if result in (None, "", [], {}):
        return True
    if isinstance(result, list):
        return not result
    if not isinstance(result, dict):
        return False
    if result.get("resolved") is False or result.get("found") is False:
        return True
    if "matched_count" in result:
        return not (result.get("matched_count") or result.get("possible_count"))
    # **Generic, and better than the list it replaced** (T-7.58). This enumerated six of THIS product's
    # container keys -- documents, citations, agreements, contracts, findings, policies -- so a second
    # product's empty result was never recognised as empty, and OUR seventh container would not have been
    # either. A result whose every collection is empty is empty, whatever the collections are called.
    collections = [value for value in result.values() if isinstance(value, (list, tuple, set, dict))]
    if collections:
        return not any(collections)
    return False


@dataclass
class WhatTheRunHasSeen:
    """One record per dispatch, and one object that holds them all.

    **One record with several readers** (ADR-0019 section 5). `RememberWhatWasCalled` writes it, the
    filesystem signpost reads it to answer the wrong turn that follows an empty result, and a later sweep
    reads it so it cannot unfind what an earlier one matched.
    """

    runs: dict[str, RunRecord] = field(default_factory=dict)
    #: Passed to every record it makes. See `RunRecord`.
    repeatable: frozenset[str] = frozenset()
    question_argument: dict[str, str] = field(default_factory=dict)

    def of(self, key: str) -> RunRecord:
        """This run's record, made on first use."""
        return self.runs.setdefault(key, RunRecord(repeatable=self.repeatable,
                                                   question_argument=self.question_argument))


def run_key(request: Any) -> str:
    """What separates one run from another, and what must NOT separate a run from itself.

    **`thread_id` first, because a run is the right scope.** `thread_for` builds it from the workspace and
    the run id, so it is shared by every dispatch of one run and never reaches another run or another
    tenant. Keying by dispatch instead was measurably too narrow: a re-dispatched worker asked four
    documents the same question in new words and matched nothing, because the two halves sat either side
    of a boundary that was guarding nothing the subject check does not already guard.

    **The namespace is the fallback, and its obvious reading is wrong.** `checkpoint_ns` is hierarchical
    and its LAST segment is the individual tool call -- measured, not assumed: two calls in one run gave
    `tools:98601b2c…` and `tools:cf1dfe0e…`. So the prefix is what identifies an invocation, and an agent
    invoked with no thread at all falls back to that.

    **`runtime.config` exists on a TOOL request and NOT on a model one** (found at T-5a.13a, measured on
    langchain 1.4.2). `ModelRequest.runtime` carries no `config` attribute at all, so every model-seam
    caller got `""` back -- a run identity that silently collapses every run in the process into one
    record. Nothing was broken by it, because both readers were tool-side; the next caller was the
    decision ledger, whose whole subject is "did THIS run do that twice". The same fact lives on the
    model side as `runtime.execution_info.thread_id`, so it is read from there rather than answered
    wrongly, and one definition of *a run* still serves both seams.
    """
    config = getattr(getattr(request, "runtime", None), "config", None) or {}
    configurable = config.get("configurable") or {} if isinstance(config, dict) else {}
    thread = str(configurable.get("thread_id") or "") or _thread_of_the_model_seam(request)
    if thread:
        return thread
    namespace = str(configurable.get("checkpoint_ns") or "")
    return namespace.rsplit("|", 1)[0] if "|" in namespace else ""


def _thread_of_the_model_seam(request: Any) -> str:
    """The thread a `ModelRequest` belongs to, or `""`.

    Degrades to silence exactly as the rest of `run_key` does: bookkeeping never fails the run it measures.
    """
    running = getattr(getattr(request, "runtime", None), "execution_info", None)
    return str(getattr(running, "thread_id", None) or "")


_QUESTION_ENCODER: list[Any] = []


def question_encoder() -> Any:
    """The engine's own query embedder (`rag_wright.api.query_embedder`), built once per process, or `None`.

    **Used to tell whether a question has already been asked** (T-5a.12h): two phrasings of one question should
    not cost two retrievals and two answer-model calls. It is the encoder retrieval itself embeds queries with, so
    "these two questions are alike" means the same here as inside a search.

    **`None` rather than a raise**: similarity is an optimisation, and without it every call simply runs. **It is
    switchable**, so it can be ruled out when a run misbehaves: `RAG_QUESTION_ENCODER=off`.
    """
    if not _QUESTION_ENCODER:
        if os.environ.get("RAG_QUESTION_ENCODER", "").lower() in {"off", "0", "false"}:
            logger.info("question_encoder_disabled_by_environment")
            _QUESTION_ENCODER.append(None)
        else:
            try:
                from rag_wright import api

                _QUESTION_ENCODER.append(api.query_embedder())
            except Exception:
                logger.warning("question_encoder_unavailable", exc_info=True)
                _QUESTION_ENCODER.append(None)
    return _QUESTION_ENCODER[0]


class RememberWhatWasCalled(AgentMiddleware):
    """Record every tool call, and answer an identical repeat from the record.

    **The saving is the point, not the commentary.** A repeat short-circuits before the operation runs, so
    it costs no retrieval, no model call inside the tool, and only a line of context instead of a second
    copy of the payload.
    """

    def __init__(self, seen: WhatTheRunHasSeen, *, encoder: Any = None,
                 carry_forward: Any = None) -> None:
        super().__init__()
        self._seen = seen
        self._encoder = encoder
        #: Read off the record rather than taken again, so the two cannot disagree about which tools
        #: are repeatable -- one fact, one place.
        self._facts = seen
        #: `(record, tool, content) -> content | None`: the PRODUCT's chance to amend a result in the
        #: light of what this run already saw. **It was `_carry_matches_forward` in this module**, which
        #: knew the tool name `sweep_matter`, the key `matched_count`, and the path
        #: `graded.conditions[].groups.matched[].document_name` -- one product's result shape, three
        #: levels deep, in generic machinery. `None` amends nothing.
        self._carry_forward = carry_forward
        self._vectors: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "RememberWhatWasCalled"

    def wrap_tool_call(self, request, handler):
        here = self._seen.of(run_key(request))
        repeat = self._already_answered(here, request)
        if repeat is not None:
            return repeat
        return self._keep(here, request, handler(request))

    async def awrap_tool_call(self, request, handler):
        here = self._seen.of(run_key(request))
        # **Encoding is moved off the event loop.** `encode_dense` is a local BGE-M3 forward pass and
        # measures 0.24-1.04s per question on CPU; run inline it blocks every other coroutine in the
        # process, including the model call it is meant to save. An optimisation that stalls the loop is
        # not an optimisation.
        await self._warm(request)
        repeat = self._already_answered(here, request)
        if repeat is not None:
            return repeat
        return self._keep(here, request, await handler(request))

    async def _warm(self, request) -> None:
        """Vectorise this call's question in a worker thread, so the comparison itself is arithmetic."""
        if self._encoder is None:
            return
        call = request.tool_call
        asked = self._facts.question_argument.get(call.get("name", ""))
        question = (call.get("args") or {}).get(asked) if asked else None
        if not question or str(question) in self._vectors:
            return
        with contextlib.suppress(Exception):  # an optimisation, never a dependency
            await asyncio.to_thread(self._vector, str(question))

    def _already_answered(self, here: RunRecord, request) -> Any:
        from langchain_core.messages import ToolMessage

        call = request.tool_call
        tool = call.get("name", "")
        if tool not in self._facts.repeatable:
            return None
        args = call.get("args") or {}
        earlier = here.identical(tool, args)
        said = "You already made this exact call in this run"
        if earlier is None:
            earlier, alike = self._same_question_asked(here, tool, args)
            if earlier is None:
                return None
            said = (f"You already asked this in different words ({alike:.2f} alike) -- "
                    f"{_the_question(earlier, self._facts.question_argument)!r}")
        return ToolMessage(
            content=(
                f"{said}, and this is what it returned. Asking again the same way will not produce a "
                "different answer. Use it, or ask something this does not cover -- naming the part you "
                "want.\n\n"
                f"{earlier.digest}"
            ),
            tool_call_id=call.get("id", ""), name=tool,
        )

    def _same_question_asked(self, here: RunRecord, tool: str,
                             args: dict[str, Any]) -> tuple[Call | None, float]:
        """An earlier call that asked the same thing of the same subject, in different words.

        **A user WILL ask the same thing twice**, and so does a worker that did not like its first
        answer -- three reads of one document, phrased three ways, in a single live dispatch. An exact
        match cannot see any of it, and waiting for a frequency to justify the fix is waiting for a
        customer to find it.

        **Same subject first, similarity second.** Everything but the question must be identical, so a
        cached answer can never cross documents; similarity only decides whether the ASK is the same one.
        """
        asked = self._facts.question_argument.get(tool)
        if asked is None or self._encoder is None or not args.get(asked):
            return None, 0.0
        subject = {k: v for k, v in args.items() if k != asked}
        best, score = None, 0.0
        for call in here.by_tool(tool):
            if {k: v for k, v in call.args.items() if k != asked} != subject:
                continue
            alike = self._alike(str(args[asked]), str(call.args.get(asked) or ""))
            if alike > score:
                best, score = call, alike
        return (best, score) if score >= THE_SAME_QUESTION else (None, score)

    def _alike(self, one: str, other: str) -> float:
        """Cosine between two questions, using the engine's own query encoder.

        **Never a reason a run fails**: an encoder that raises makes everything unalike, which costs a
        repeated call and nothing else.
        """
        try:
            first, second = self._vector(one), self._vector(other)
            if first is None or second is None:
                return 0.0
            return float(sum(a * b for a, b in zip(first, second, strict=True)))
        except Exception:  # noqa: BLE001 -- similarity is an optimisation, never a dependency
            return 0.0

    def _vector(self, text: str) -> list[float] | None:
        if text not in self._vectors:
            dense = self._encoder.encode_dense(text)
            dense = dense[0] if isinstance(dense, tuple) else dense
            values = [float(x) for x in dense]
            length = sum(x * x for x in values) ** 0.5 or 1.0
            self._vectors[text] = [x / length for x in values]
        return self._vectors[text]

    def _keep(self, here: RunRecord, request, result: Any) -> Any:
        call = request.tool_call
        tool = call.get("name", "")
        args = call.get("args") or {}
        content = _content_of(result)
        amended = ((self._carry_forward(here, tool, content) if self._carry_forward else None)
                   or self._what_was_already_asked(here, tool, args, content))
        here.remember(tool, args, content)
        return _replacing(result, amended) if amended is not None else result

    def _what_was_already_asked(self, here: RunRecord, tool: str, args: dict[str, Any],
                                content: Any) -> Any:
        """Tell a related question what this subject has already been asked.

        **Between "the same question" and "unrelated" is where a spiral lives.** Substituting an answer
        here would risk answering the wrong question, so the call runs -- but a result that does not say
        what was already established invites a third phrasing, which is exactly what happened live.
        """
        asked = self._facts.question_argument.get(tool)
        if asked is None or self._encoder is None or not isinstance(content, dict) or not args.get(asked):
            return None
        subject = {k: v for k, v in args.items() if k != asked}
        related = [
            call for call in here.by_tool(tool)
            if {k: v for k, v in call.args.items() if k != asked} == subject
            and WORTH_MENTIONING <= self._alike(str(args[asked]), _the_question(call, self._facts.question_argument))
        ]
        if not related:
            return None
        already = "; ".join(f"{_the_question(call, self._facts.question_argument)!r} -> {call.digest[:160]}" for call in related[-2:])
        return {**content, "already_asked_here": (
            f"You have already asked {len(related)} related question(s) of this subject in this "
            f"dispatch: {already}. If that is the same ground, stop here rather than rephrasing again."
        )}


class OneAnswerPerTurn(AgentMiddleware):
    """Collapse a model response that repeats one call, before the framework reacts to each copy.

    **Live, one turn emitted the structured response 726 times.** LangChain raises
    `MultipleStructuredOutputsError` and emits one `ToolMessage` per call, so 726 identical errors entered
    the context; that single turn cost **25,393 output tokens**; the worker then restarted from its first
    tool and the orchestrator dispatched twice more. One task: 549 seconds, 104k input and 30k output
    tokens, for work it had already finished correctly by its third turn.

    **The framework detects the condition and reports it. Nothing stops it**, and nothing keeps 726 copies
    of the report out of the context -- so the guard has to sit between the model and the framework, which
    is what `wrap_model_call` is.

    **Identity is `(name, arguments)`, never the name alone.** Five reads of five documents in one turn is
    the behaviour we want; collapsing on name would destroy the parallel fan-out that makes a worker
    efficient. Only an exactly repeated call is repetition.
    """

    @property
    def name(self) -> str:
        return "OneAnswerPerTurn"

    def wrap_model_call(self, request, handler):
        return self._once(handler(request))

    async def awrap_model_call(self, request, handler):
        return self._once(await handler(request))

    def _once(self, response: Any) -> Any:
        result = getattr(response, "result", None)
        if not result:
            return response
        settled = [self._settled(message) for message in result]
        if all(one is other for one, other in zip(settled, result, strict=True)):
            return response
        response.result = settled
        return response

    def _settled(self, message: Any) -> Any:
        """The message with exact repeats of a call removed, or the message itself if there are none."""
        calls = getattr(message, "tool_calls", None) or []
        if len(calls) < 2:
            return message
        kept, seen = [], set()
        for call in calls:
            key = (call.get("name"), json.dumps(call.get("args"), sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            kept.append(call)
        if len(kept) == len(calls):
            return message
        logger.warning("model_repeated_one_call name=%s emitted=%d kept=%d",
                       calls[0].get("name"), len(calls), len(kept))
        trimmed = message.model_copy() if hasattr(message, "model_copy") else message
        trimmed.tool_calls = kept
        return trimmed


def _the_question(call: Call, question_argument: dict[str, str]) -> str:
    """The question text of a call, by the product's own argument name for it."""
    return str(call.args.get(question_argument.get(call.tool, ""), ""))






def _replacing(result: Any, content: Any) -> Any:
    """The same tool message carrying amended content."""
    if hasattr(result, "content"):
        result.content = json.dumps(content, default=str)
        return result
    return content


def _content_of(result: Any) -> Any:
    """What a tool actually answered, out of whatever the middleware chain hands back."""
    content = getattr(result, "content", result)
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return content
    return content
