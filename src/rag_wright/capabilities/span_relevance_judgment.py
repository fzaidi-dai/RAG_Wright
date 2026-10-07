"""Engine issue 0023: a per-span RELEVANCE VERDICT on the corpus retrieval path.

`typed_property_retrieval` always returns the top-k nearest spans, so `not_found` was unreachable and a nonsense
query still returned a full page of clauses. No SCORE fixes this: RRF is a relabeling of the row number, cosine's
distribution moves with model/domain/chunking, and a cross-encoder is a better number but still a number to
threshold -- every threshold is a corpus-specific magic knob that fails silently. The generic answer is a VERDICT
(the shape the compliance judge and answer-abstention already use): the engine decides "does this span address
this condition?" and returns the FACT; the product keeps the matched/possible/not_found grouping (POLICY).

SKILL-SPLIT (mirrors `compliance_judgment`):
- **`span_relevance_judgment` (agent_skill)** -- the LLM relevance METHOD, authored as
  `skills/span_relevance_judgment/SKILL.md`, applied through the model seam. Given one span's text + the
  structured `Condition` (+ the typed properties detected on the span, as CONTEXT not proof), returns a raw
  `RelevanceVerdict`. `build_arelevance_judge_fn` is its runtime; `structured_factory` is injected for tests.
- The applying capability owns the guarantees the skill does not: the closed verdict vocab and the CONSERVATIVE
  DEFAULT -- an unreadable/failed judgement maps to `uncertain` (recall-safe: a judge failure never fabricates a
  `not_found`; the span stays visible), exactly as the compliance judge defaults to `needs_review`.

`ajudge_spans` runs the judge over a retrieved set concurrently (async + semaphore + a wall-clock bound per span),
so a sweep's wall-clock is concurrency-bound, not count-bound (the parallel-LLM rule).
"""

from __future__ import annotations

import asyncio
import os
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Optional

from pydantic import BaseModel

from rag_wright.models.seam import build_structured

_SKILL_PATH = Path(__file__).parents[1] / "skills" / "span_relevance_judgment" / "SKILL.md"


class Relevance(str, Enum):
    """The closed relevance vocab. `uncertain` is both a real judgement (ambiguous text) AND the conservative
    default when the judge could not be read (recall-safe: never a fabricated not_found)."""

    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    UNCERTAIN = "uncertain"


_VERDICTS = {v.value for v in Relevance}


class Condition(BaseModel):
    """The structured test a retrieved span is judged against (issue 0023). `clause_type` is primary (a category
    to test membership of); `value_condition` is a narrower test within it (often absent or shared across a
    multi-condition sweep); `question` is CONTEXT ONLY -- in a multi-condition sweep it belongs to all conditions
    at once, so it must not by itself make a span relevant. (Per-condition question decomposition is a separate
    engine gap, not owned here.)"""

    clause_type: str
    value_condition: Optional[str] = None
    question: Optional[str] = None


class RelevanceVerdict(BaseModel):
    """The raw structured output of one relevance judgement (the `span_relevance_judgment` SKILL's typed output).
    `verdict` is a loose string mapped to the closed `Relevance` vocab by the applying capability (unreadable ->
    uncertain, the conservative default)."""

    verdict: str
    rationale: str = ""
    confidence: float = 0.0


# ajudge_fn: (span_text, matched_constraints, condition) -> RelevanceVerdict. Primitives (not RankedSpan) so this
# capability stays independent of the retrieval contract -- the subgraph adapts its spans to these inputs.
AJudgeFn = Callable[[str, list[tuple[str, str]], Condition], Awaitable[RelevanceVerdict]]


def _skill_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        marker = text.find("\n---", 3)
        if marker != -1:
            text = text[marker + 4 :]
    return text.strip()


def relevance_method() -> str:
    """The authored relevance-judgment method (skills/span_relevance_judgment/SKILL.md)."""
    return _skill_body(_SKILL_PATH)


_PROMPT_TAIL = (
    "\n\nCONDITION being searched for:\n"
    "- clause type: {clause_type}\n"
    "- specific condition: {value_condition}\n"
    "- user's question (CONTEXT ONLY -- may be shared across several conditions): {question}\n\n"
    "TYPED PROPERTIES already detected on this span (CONTEXT -- extracted from the question and reused across "
    "conditions, so evidence, NOT proof of relevance; judge the span TEXT):\n{matched}\n\n"
    "RETRIEVED SPAN (the only text you may judge):\n{span}"
)


def _tail(span_text: str, matched: list[tuple[str, str]], condition: Condition) -> str:
    matched_str = ", ".join(f"{d} = {v}" for d, v in matched) if matched else "(none)"
    return _PROMPT_TAIL.format(
        clause_type=condition.clause_type,
        value_condition=condition.value_condition or "(none -- judge against the clause type)",
        question=condition.question or "(none)",
        matched=matched_str,
        span=span_text,
    )


def build_arelevance_judge_fn(model_id: str, *, structured_factory=build_structured) -> AJudgeFn:
    """The `span_relevance_judgment` SKILL runtime: an async relevance judge through the model seam. Given a span's
    text + the typed properties detected on it (context) + the structured condition, returns a raw
    `RelevanceVerdict`. `structured_factory` is injected for hermetic tests."""
    method = relevance_method()

    async def judge(span_text: str, matched: list[tuple[str, str]], condition: Condition) -> RelevanceVerdict:
        # label names the generation (ADR-0058 / issue 0025) so a reader tells `span-relevance` from
        # `query-constraints` in the cost report; ignored by the hermetic stub factory.
        return await structured_factory(model_id, RelevanceVerdict, label="span-relevance").ainvoke(
            method + _tail(span_text, matched, condition))

    return judge


def to_relevance(raw: str) -> Relevance:
    """Map the LLM's loose verdict string to the closed vocab; unreadable -> uncertain (conservative default:
    recall-safe, so a mis-read never fabricates a not_found)."""
    value = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return Relevance(value) if value in _VERDICTS else Relevance.UNCERTAIN


def finalize_verdict(raw: Optional[RelevanceVerdict]) -> RelevanceVerdict:
    """The deterministic guarantees the SKILL does not own (the applying-capability step): map the raw verdict to
    the closed vocab (unreadable/None -> `uncertain`, the conservative recall-safe default) and clamp confidence to
    [0, 1]. Mirrors `compliance_judgment.assemble_finding`'s conservative mapping."""
    if raw is None:
        return RelevanceVerdict(verdict=Relevance.UNCERTAIN.value, rationale="relevance judge did not rule",
                                confidence=0.0)
    return RelevanceVerdict(verdict=to_relevance(raw.verdict).value, rationale=raw.rationale,
                            confidence=min(1.0, max(0.0, raw.confidence)))


_JUDGE_TIMEOUT_S = float(os.environ.get("RAG_RELEVANCE_TIMEOUT_S", "90"))  # per-span wall-clock bound


async def ajudge_spans(
    spans: list[tuple[str, list[tuple[str, str]]]], condition: Condition, *, ajudge_fn: AJudgeFn,
    max_concurrency: int = 8, timeout_s: float | None = _JUDGE_TIMEOUT_S, timeout_retries: int = 1,
) -> list[RelevanceVerdict]:
    """Judge many retrieved spans against one condition CONCURRENTLY (asyncio.gather + Semaphore, the parallel-LLM
    rule), order preserved. Each `spans` item is `(span_text, matched)`. Each judgement is bounded by `timeout_s`
    (a hard wall-clock deadline); on the deadline it is retried up to `timeout_retries` times, then falls back to a
    conservative `uncertain` verdict -- a stalled provider never hangs the sweep. A NON-timeout judge error
    propagates (a genuine bug is never masked). Every returned span gets a verdict: the list is 1:1 with `spans`."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _rule(span_text: str, matched: list[tuple[str, str]]) -> RelevanceVerdict:
        if timeout_s is None:
            return await ajudge_fn(span_text, matched, condition)
        for attempt in range(timeout_retries + 1):
            try:
                async with asyncio.timeout(timeout_s):
                    return await ajudge_fn(span_text, matched, condition)
            except (asyncio.TimeoutError, TimeoutError):
                if attempt >= timeout_retries:
                    return RelevanceVerdict(verdict=Relevance.UNCERTAIN.value,
                                            rationale="relevance judge timed out", confidence=0.0)
        return RelevanceVerdict(verdict=Relevance.UNCERTAIN.value, rationale="relevance judge timed out")

    async def _one(item: tuple[str, list[tuple[str, str]]]) -> RelevanceVerdict:
        span_text, matched = item
        async with semaphore:
            return await _rule(span_text, matched)

    return list(await asyncio.gather(*(_one(item) for item in spans)))


def register_span_relevance_judgment(registry) -> None:
    """Register `span_relevance_judgment` as an AGENT_SKILL (issue 0023, ADR-0088): a single grounded LLM relevance
    judgement, authored as `skills/span_relevance_judgment/SKILL.md` and applied via the seam. Output =
    `RelevanceVerdict`. Promoted to a canonical FR-C slug (`registry.ENGINE_CAPABILITY_SLUGS`), the same way every
    post-v0.1 capability (compliance module, KG-primary retrieval core) was added -- registry mirror + ADR."""
    registry.register(
        "span_relevance_judgment",
        contract=RelevanceVerdict,
        kind="agent_skill",
        display_name="Span relevance judgment (span x condition -> verdict; authored skill)",
    )
