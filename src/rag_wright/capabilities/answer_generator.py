"""Answer generation (FR-C.9, FR-Q.6, T29): grounded, cited, confidence-aware, abstention-willing.

Produces the final answer from the query-side evidence (fusion/synthesis). It enforces the spec's hard
rule — **no claim without a citation** (FR-Q.6): every non-abstaining answer must cite `chunk_id`s that
are actually in the evidence, and a question the evidence does not support yields an **abstention**, not
a fabrication. It is **confidence-aware**: graph-derived facts carry their confidence tag into the
evidence the model sees (T26 surfaces it; here it is put in front of the generator). Vision-to-text is a
separate capability/slug (`vision_to_text.py`, ADR-0014), though both run on the Gemma 4 class model.

Grounding and citation are enforced in CODE around the model, not left to the prompt: fabricated
citations (ids not in the evidence) are dropped, and an answer that ends up with no valid citation is
coerced to an abstention. The model choice is the `GENERAL` role (no flag here).
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, model_validator

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model, build_structured
from rag_wright.util.concurrent import map_concurrent

_ABSTENTION = "The retrieved context does not support an answer."
_SKILL_PATH = Path(__file__).parents[1] / "skills" / "generation" / "SKILL.md"


def generation_method() -> str:
    """The grounded-answer method (the `generation` SKILL body, YAML frontmatter stripped) used as the
    generator's instruction. Authored knowledge (skills/generation/SKILL.md), not a hardcoded string. The
    citation/abstention GUARANTEES are still enforced in code around the model (see `generate_answer`)."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        marker = text.find("\n---", 3)
        if marker != -1:
            text = text[marker + 4 :]
    return text.strip()


class EvidenceItem(BaseModel):
    """One piece of grounding evidence: a chunk's text, its `chunk_id` (the citation), and — for a
    graph-derived fact — its confidence tag (surfaced to the generator, FR-S.4)."""

    chunk_id: str
    text: str
    confidence: Optional[str] = None  # graph-fact confidence tag; None for plain retrieved text


class AnswerKind(str, Enum):
    """The sufficiency of a generated answer (PREC-1a): a first-class signal so an honest hedge is distinct
    from a confident over-answer, both in the contract the caller receives and in evaluation."""

    ANSWERED = "answered"      # the evidence supports the answer
    PARTIAL = "partial"        # answered, but the evidence does NOT fully support it -> caveated, low-confidence
    ABSTAINED = "abstained"    # the evidence supports no answer -> abstention (no fabrication)


class GeneratedAnswer(BaseModel):
    """The generated answer (FR-C.9): grounded text, the cited chunk_ids, whether it abstained, and its
    sufficiency `answer_kind` (PREC-1a). `abstained` is kept (backward-compat) and `answer_kind` is kept in
    sync: constructing with `abstained` alone derives the kind (ABSTAINED/ANSWERED); passing `answer_kind`
    (e.g. PARTIAL) wins and sets `abstained` accordingly. So no existing `abstained=`-only caller changes."""

    answer: str
    citations: list[str]  # chunk_ids actually in the evidence (no claim without a citation, FR-Q.6)
    abstained: bool = False  # kept for backward-compat; reconciled with answer_kind by the validator below
    answer_kind: Optional[AnswerKind] = None  # None at input -> derived from `abstained`; else it wins

    @model_validator(mode="after")
    def _sync_kind(self) -> "GeneratedAnswer":
        if self.answer_kind is None:
            self.answer_kind = AnswerKind.ABSTAINED if self.abstained else AnswerKind.ANSWERED
        else:
            self.abstained = self.answer_kind is AnswerKind.ABSTAINED
        return self


@runtime_checkable
class AnswerModel(Protocol):
    """The generation seam: produce a `GeneratedAnswer` for a grounded prompt (structured output)."""

    def generate(self, prompt: str) -> GeneratedAnswer: ...


class SeamAnswerModel:
    """The real generator: structured output through the model-profile seam (GENERAL role). `temperature`
    defaults to 0; the best-of-N strategy constructs one at temperature>0 to sample diverse completions."""

    def __init__(
        self, model_id: str | None = None, *, temperature: float = 0.0, max_tokens: int | None = None
    ) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)
        self._temperature = temperature
        self._max_tokens = max_tokens

    def generate(self, prompt: str) -> GeneratedAnswer:
        return build_structured(
            self._model_id, GeneratedAnswer, temperature=self._temperature, max_tokens=self._max_tokens
        ).invoke(prompt)


@runtime_checkable
class ReasonModel(Protocol):
    """The free-text reasoning seam (B): analyze the evidence in prose, no forced schema."""

    def reason(self, prompt: str) -> str: ...


class SeamReasonModel:
    """The real reasoner: a plain free-text call through the seam (GENERAL role). Free-text avoids the
    forced-structured/thinking-mode conflict that makes the one-shot structured generate flaky."""

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)

    def reason(self, prompt: str) -> str:
        return str(build_model(self._model_id).invoke(prompt).content)


# --- client-side structured output: free-text + light XML tags, parsed here (no server guided decoding) ------
#
# Why: on some serving stacks (self-hosted Gemma 4 on vLLM) SERVER-SIDE grammar-constrained structured output
# runs away to max_model_len, while plain FREE-TEXT terminates cleanly. So we ask the model to answer in light
# XML tags and parse them CLIENT-SIDE into GeneratedAnswer. Tags (not JSON) because the `answer` body is long
# legal prose full of quotes/brackets/newlines -- which is exactly what breaks JSON string escaping; a tagged
# body needs no escaping. Robust by design: a missing <citations> block falls back to the inline [chunk_id]s the
# model already emits, and missing tags fall back to treating the whole text as the answer -- so retries are rare
# and _finalize (drop non-evidence citations, coerce uncited -> abstain) still enforces the contract downstream.

_TAG_INSTRUCTIONS = (
    "\n\nReturn your response using EXACTLY these tags:\n"
    "<answer>\nYour grounded answer, citing each supporting evidence item inline as [chunk_id] (the bracketed "
    "id shown for that item).\n</answer>\n"
    "<citations>\nThe chunk_id of every evidence item you used, one per line; use only ids present in the "
    "evidence above.\n</citations>\n"
    "If the evidence does not support an answer at all, output exactly <abstain/> and nothing else. If the "
    "evidence only PARTIALLY or TANGENTIALLY addresses the question -- it mentions related material but does "
    "not actually state the answer -- give what the evidence does support with citations, add the marker "
    "<partial/>, and say plainly what the evidence does not establish (do NOT present a tangential mention as a "
    "confident answer)."
)
_ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)
_CITE_BLOCK_RE = re.compile(r"<citations>(.*?)</citations>", re.DOTALL | re.IGNORECASE)
_ABSTAIN_RE = re.compile(r"<abstain\s*/?>", re.IGNORECASE)
_PARTIAL_RE = re.compile(r"<partial\s*/?>", re.IGNORECASE)
# an inline citation: [<contract_id>:<index>:<hex hash>]; contract_id is delimiter-safe (no brackets).
_INLINE_CITE_RE = re.compile(r"\[([^\[\]]+:\d+:[0-9a-fA-F]{8,})\]")


def parse_tagged_answer(text: str) -> GeneratedAnswer:
    """Parse a free-text tagged response into a GeneratedAnswer (Pydantic then validates the contract; the
    capability's _finalize drops any citation not in the evidence). Tolerant: <answer> tag -> its body; else an
    <abstain/> marker -> abstain; else the whole text is the answer. Citations come from the <citations> block
    AND the inline [chunk_id]s in the answer body (deduped), so a missing block still yields citations."""
    t = text.strip()
    if not t:
        return _abstain()
    match = _ANSWER_RE.search(t)
    if match:
        answer = match.group(1).strip()
    elif _ABSTAIN_RE.search(t):
        return _abstain()
    else:
        answer = _PARTIAL_RE.sub("", t).strip()  # no tags -> prose (with inline [chunk_id]s); drop any marker
    if not answer:
        return _abstain()
    citations: list[str] = []
    block = _CITE_BLOCK_RE.search(t)
    if block:
        citations = [c for c in re.split(r"[\s,]+", block.group(1).strip()) if c]
    for cid in _INLINE_CITE_RE.findall(answer):  # supplement with inline ids (dedup, order-preserving)
        if cid not in citations:
            citations.append(cid)
    kind = AnswerKind.PARTIAL if _PARTIAL_RE.search(t) else AnswerKind.ANSWERED  # a flagged partial/hedge
    return GeneratedAnswer(answer=answer, citations=citations, answer_kind=kind)


class TaggedFreeTextAnswerModel:
    """Generation with NO server-side guided decoding: a plain free-text call (`build_model`, no
    `response_format`) that the model answers in light XML tags, parsed client-side (`parse_tagged_answer`).
    `max_tokens` is a generous safety cap only -- free-text terminates on its own."""

    def __init__(
        self, model_id: str | None = None, *, temperature: float = 0.0, max_tokens: int = 2048
    ) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)
        self._temperature = temperature
        self._max_tokens = max_tokens

    def generate(self, prompt: str) -> GeneratedAnswer:
        text = build_model(
            self._model_id, temperature=self._temperature, max_tokens=self._max_tokens
        ).invoke(prompt + _TAG_INSTRUCTIONS).content
        return parse_tagged_answer(str(text))


def answer_model_for(
    model_id: str | None = None, *, temperature: float = 0.0, max_tokens: int | None = None
) -> AnswerModel:
    """The generation strategy for a model: ALWAYS the free-text + client-side tag-parse path (ADR-0045).
    Server-side guided decoding is not portable (runs away on self-hosted Gemma 4, ~60s/call on Cerebras), so
    generation no longer depends on it for any model -- one LLM-agnostic path, so production and evals stay in
    step. `SeamAnswerModel` remains for an explicit opt-in (constructed directly), but is never the default."""
    mid = model_id or model_for(ModelRole.GENERAL)
    return TaggedFreeTextAnswerModel(mid, temperature=temperature, max_tokens=max_tokens or 2048)


def _evidence_block(evidence: list[EvidenceItem]) -> str:
    # engine issue 0002 (ADR-0055): NO inline [confidence: ...] marker. It used to sit in the evidence text,
    # where the model narrated it to the reader (~100% conditional on citing an uncertain clause). Confidence is
    # now delivered out-of-band as a hedging directive (see `_confidence_directive`); the evidence block is just
    # the cited text.
    return "\n".join(f"[{item.chunk_id}] {item.text}" for item in evidence)


# Confidence, OUT-OF-BAND (engine issue 0002 / ADR-0055). Instead of an inline [confidence: ...] marker the model
# can quote, the worst-case certainty across the evidence becomes a HEDGING DIRECTIVE the prompt consumes -- a
# tone instruction, appended after the evidence, never quotable. It does NOT name the internal enum tokens
# (INFERRED / AMBIGUOUS), so they cannot be echoed. This preserves the FR-S.4 / ADR-0028 hedging while removing
# the narratable surface -- the same move that closed the auto-tag leak (ADR-0054).
def _confidence_directive(evidence: list[EvidenceItem]) -> str:
    confs = {(item.confidence or "").upper() for item in evidence}
    if "AMBIGUOUS" in confs:
        return ("\n\nCertainty note (do NOT mention this to the reader): some of the evidence is uncertain. "
                "Where your answer depends on it, be tentative and do not state those points as settled. Let this "
                "shape only how tentatively you write; never mention certainty, confidence, or any internal label.")
    if "INFERRED" in confs:
        return ("\n\nCertainty note (do NOT mention this to the reader): some of the evidence is inferred rather "
                "than directly stated. Present any point that depends on it as an inference, not a settled fact. "
                "Let this shape only how you phrase it; never mention certainty, confidence, or any internal label.")
    return ""


def _abstain(text: str = _ABSTENTION) -> GeneratedAnswer:
    return GeneratedAnswer(answer=text, citations=[], abstained=True)


# --- output hygiene: keep the engine's internal annotations out of user-facing prose (engine issue 0001) -----
#
# The evidence block feeds the model machine-internal markers -- inline citation ids [id:idx:hash], the
# [auto-tag: TYPE] classification (the engine's own sometimes-wrong guess), the [confidence: ...] tag, the
# [dimension=value; ...] typed-property string, and the [Exception ... (inferred)] carve-out framing. These are
# INPUTS to the model's judgement; a reader must never see them (a narrated auto-tag asserts a possibly-wrong
# clause type in the engine's voice, and a raw id looks broken). Citation ids belong in `citations` only. The
# SKILL now tells the model not to narrate them; this code is the hard guarantee for the bracketed forms it may
# still echo. TARGETED, not a blanket bracket strip: only the known annotation formats and the exact evidence
# chunk_ids are removed, so a legitimately quoted bracket (a defined term like "[Party A]") survives.
_CID_SHAPE = re.compile(r"^[^\[\]]+:\d+:[0-9a-fA-F]{8,}$")
_PROSE_ANNOTATION_RES = [
    re.compile(r"\[[^\[\]]+:\d+:[0-9a-fA-F]{8,}\]"),   # a bracketed citation id (incl. a fabricated one)
    re.compile(r"\[auto-tag:[^\[\]]*\]", re.IGNORECASE),
    re.compile(r"\[confidence:[^\[\]]*\]", re.IGNORECASE),
    re.compile(r"\[Exception[^\[\]]*\]", re.IGNORECASE),  # the inferred carve-out framing
    re.compile(r"\[[^\[\]]*=[^\[\]]*\]"),                 # a typed-property fact group [dim=value; ...]
    # engine issue 0002: a literal schema FIELD NAME written where a citation would go (not an id) -- engine
    # vocabulary, never legitimate in a contract answer.
    re.compile(r"\[(?:chunk_id|clause_id|source_doc_id|span_id|answer_kind)\]", re.IGNORECASE),
]


def _scrub_prose(text: str, evidence: list[EvidenceItem]) -> str:
    """Remove the engine's internal annotation tokens from user-facing answer prose (issue 0001): the exact
    evidence chunk_ids (bracketed and, for citation-shaped ids, bare), then the known bracketed annotation
    formats, then tidy the whitespace/punctuation the removals leave behind. Quoted clause text and any other
    bracketed text are left intact -- only the known formats and the exact ids are stripped."""
    out = text
    for item in evidence:  # the exact ids we know are in play (precise; avoids guessing)
        out = re.sub(rf"\[\s*{re.escape(item.chunk_id)}\s*\]", "", out)
        if _CID_SHAPE.match(item.chunk_id):  # bare removal only for real citation-shaped ids (not short test ids)
            out = out.replace(item.chunk_id, "")
    for rx in _PROSE_ANNOTATION_RES:
        out = rx.sub("", out)
    out = re.sub(r"\(\s*\)", "", out)             # empty parens left by a removed token
    out = re.sub(r"[ \t]{2,}", " ", out)          # collapse runs of spaces
    out = re.sub(r"[ \t]+([,.;:)])", r"\1", out)  # no space before punctuation
    out = re.sub(r"\n[ \t]+", "\n", out)
    return out.strip()


def _finalize(raw: GeneratedAnswer, evidence: list[EvidenceItem]) -> GeneratedAnswer:
    """The code-level guarantees applied to a raw model answer (shared by every generation strategy):
    an abstention stays an abstention; a citation not present in the evidence is dropped (no fabrication);
    an answer left with no valid citation is coerced to an abstention (no claim without a citation, FR-Q.6);
    and the answer prose is scrubbed of internal annotations/ids (issue 0001) -- if that leaves no readable
    prose, abstain rather than return an empty answer."""
    if raw.abstained:
        return _abstain(raw.answer or _ABSTENTION)
    valid_ids = {item.chunk_id for item in evidence}
    citations = [chunk_id for chunk_id in raw.citations if chunk_id in valid_ids]  # drop fabricated
    if not citations:
        return _abstain()  # no valid citation -> abstain (even a PARTIAL needs a citation, FR-Q.6)
    answer = _scrub_prose(raw.answer, evidence)  # keep internal annotations/ids out of the reader's prose
    if not answer:
        return _abstain()  # the prose was nothing but annotations -> abstain
    return GeneratedAnswer(answer=answer, citations=citations, answer_kind=raw.answer_kind)


def _answer_prompt(query: str, evidence: list[EvidenceItem]) -> str:
    return (f"{generation_method()}\n\nQuestion: {query}\n\nEvidence:\n{_evidence_block(evidence)}"
            f"{_confidence_directive(evidence)}")


def generate_answer(
    query: str, evidence: list[EvidenceItem], *, model: AnswerModel
) -> GeneratedAnswer:
    """Generate a grounded, cited answer — or abstain — enforcing no-claim-without-a-citation in code.

    Empty evidence abstains without a model call. Otherwise the model answers over the evidence block
    (with confidence tags surfaced); any citation not present in the evidence is dropped, and an answer
    left with no valid citation is coerced to an abstention. This is the single-call baseline strategy.
    """
    if not evidence:
        return _abstain()
    return _finalize(model.generate(_answer_prompt(query, evidence)), evidence)


_REASON_HEADER = (
    "STEP 1 — ANALYSIS (not the final answer). Work through ONLY the evidence below: does it support an "
    "answer to the question? Name the specific [chunk_id] items that support each part of a would-be answer; "
    "if an item is framed as an inferred exception/carve-out, note the rule together with its exception. If "
    "the evidence genuinely does not support an answer, say so and why. Do NOT write the final answer yet."
)


def generate_answer_reasoned(
    query: str, evidence: list[EvidenceItem], *, reason_model: ReasonModel, emit_model: AnswerModel
) -> GeneratedAnswer:
    """Strategy B: split generation into a FREE-TEXT reasoning node then a STRUCTURED emit node. The reason
    node analyzes the evidence in prose (where the model is strongest and the forced-structured/thinking-mode
    conflict does not apply); the emit node only FORMATS that conclusion into the `GeneratedAnswer` contract,
    a more constrained call than reason-and-emit in one shot. Same code-level guarantees via `_finalize`;
    empty evidence still abstains without any model call."""
    if not evidence:
        return _abstain()
    block = _evidence_block(evidence)
    directive = _confidence_directive(evidence)  # out-of-band hedging (ADR-0055), applied to both nodes
    analysis = reason_model.reason(
        f"{generation_method()}\n\n{_REASON_HEADER}\n\nQuestion: {query}\n\nEvidence:\n{block}{directive}")
    emit_prompt = (
        f"{generation_method()}\n\nQuestion: {query}\n\nEvidence:\n{block}{directive}\n\n"
        f"STEP 2 — using your STEP 1 analysis below, emit the final grounded, cited answer now, or abstain "
        f"if the analysis concluded the evidence does not support one.\n\nSTEP 1 analysis:\n{analysis}"
    )
    return _finalize(emit_model.generate(emit_prompt), evidence)


def generate_answer_best_of_n(
    query: str, evidence: list[EvidenceItem], *, model: AnswerModel, n: int = 5, min_answers: int = 1,
    max_concurrency: int = 5,
) -> GeneratedAnswer:
    """Strategy C: sample the single-call generation `n` times (supply a temperature>0 `model` for genuine
    diversity), run CONCURRENTLY, and take the best-cited NON-abstaining sample — abstaining only if fewer
    than `min_answers` samples produced a valid cited answer. Self-consistency against the near-boundary
    abstain flip: one good grounded sample is enough to answer; `min_answers`>1 demands agreement. Empty
    evidence abstains without any model call."""
    if not evidence:
        return _abstain()
    prompt = _answer_prompt(query, evidence)
    raws = map_concurrent([prompt] * n, model.generate, max_concurrency=max_concurrency)
    answered = [f for f in (_finalize(r, evidence) for r in raws if r is not None) if not f.abstained]
    if len(answered) < min_answers:
        return _abstain()
    return max(answered, key=lambda f: len(f.citations))


def register_generation(registry: CapabilityRegistry) -> None:
    """Register answer generation under FR-C.9 (`generation`; vision-to-text is its own slug, ADR-0014)."""
    registry.register(
        "generation",
        contract=GeneratedAnswer,
        kind="agent_skill",  # a single grounded/cited LLM act (CAP-REG-1)
        display_name="Answer generation (grounded, cited, abstains)",
    )
