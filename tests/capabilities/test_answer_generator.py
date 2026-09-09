"""Answer generation + vision-to-text (T29, FR-C.9/FR-Q.6).

Hermetic tests prove the hard grounding rules in code (no claim without a citation, abstention, dropping
fabricated citations, surfacing confidence) and the vision seam. Live `-m model` runs the real Gemma
generation and a real image transcription (a synthetic PNG).
"""

from __future__ import annotations

import io
import threading

import pytest

from rag_wright.capabilities.answer_generator import (
    EvidenceItem,
    GeneratedAnswer,
    _evidence_block,
    _scrub_prose,
    generate_answer,
    generate_answer_best_of_n,
    generate_answer_reasoned,
    register_generation,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.vision_to_text import vision_to_text


class _StubModel:
    def __init__(self, answer: GeneratedAnswer) -> None:
        self._answer = answer
        self.prompt: str | None = None

    def generate(self, prompt: str) -> GeneratedAnswer:
        self.prompt = prompt
        return self._answer


class _RaisingModel:
    def generate(self, prompt: str) -> GeneratedAnswer:
        raise AssertionError("the model must not be called when there is no evidence")


_EV = [EvidenceItem(chunk_id="c1", text="Acme and Beta are the parties.")]


# --- grounding, citation, abstention -------------------------------------------------------------


def test_cited_answer_passes_through():
    model = _StubModel(GeneratedAnswer(answer="Acme and Beta.", citations=["c1"], abstained=False))
    result = generate_answer("Who are the parties?", _EV, model=model)
    assert result.answer == "Acme and Beta." and result.citations == ["c1"] and not result.abstained


def test_empty_evidence_abstains_without_calling_the_model():
    result = generate_answer("Who are the parties?", [], model=_RaisingModel())
    assert result.abstained and result.citations == []  # abstain, no fabrication, no model call


def test_answer_with_no_citation_is_coerced_to_abstention():
    model = _StubModel(GeneratedAnswer(answer="Acme and Beta.", citations=[], abstained=False))
    result = generate_answer("q", _EV, model=model)
    assert result.abstained and result.citations == []  # no claim without a citation (FR-Q.6)


def test_fabricated_citations_are_dropped():
    model = _StubModel(GeneratedAnswer(answer="Acme.", citations=["c1", "c99"], abstained=False))
    result = generate_answer("q", _EV, model=model)
    assert result.citations == ["c1"]  # c99 is not in the evidence -> dropped


# --- output hygiene: internal annotations never leak into user-facing prose (engine issue 0001) ---

_CID = "ffe378605789f4540ff1877d1e9f87b6:2:6ff1e26368b002de57829719038499e2ef60057c938df4ef9404aec4c0051eda"


def test_prose_scrubbed_of_ids_and_annotations():
    """GeneratedAnswer.answer is prose for a person: no chunk_id substring, no bracketed annotation groups
    ([auto-tag]/[confidence]/[dim=value]); citations still carry the id; the real clause quote survives."""
    ev = [EvidenceItem(
        chunk_id=_CID,
        text="[auto-tag: Liquidated Damages] Supplier's total liability shall not exceed the fees paid "
             "[cap_quantum=12_months; cap_basis=multiple_of_fees]",
        confidence="AMBIGUOUS")]
    dirty = GeneratedAnswer(
        answer=('The cap is the fees paid in the prior twelve months, from the clause "Supplier\'s total '
                'liability under this Agreement shall not exceed the fees paid". This is tagged '
                '[auto-tag: Liquidated Damages] with [confidence: AMBIGUOUS] and '
                f'[cap_quantum=12_months; cap_basis=multiple_of_fees]. [{_CID}]'),
        citations=[_CID], abstained=False)
    result = generate_answer("What is the limitation of liability?", ev, model=_StubModel(dirty))
    assert result.citations == [_CID]                       # the id still lives in `citations`
    assert _CID not in result.answer                        # ...but never in the prose
    assert "[auto-tag" not in result.answer
    assert "[confidence" not in result.answer
    assert "cap_quantum" not in result.answer
    assert "[" not in result.answer and "]" not in result.answer  # no annotation groups survive
    assert "shall not exceed the fees paid" in result.answer      # the real clause quote is kept
    assert "  " not in result.answer and " ." not in result.answer  # prose stays tidy after removal


def test_scrub_repairs_punctuation_orphaned_by_a_removed_citation_list():
    # issue 0022: removing the markers of a citation LIST leaves the separating comma orphaned before the terminal
    # punctuation (",." / a trailing ","). Repair it, alongside the empty-paren / space-before-punct passes.
    ev = [EvidenceItem(chunk_id="docA:0:aaa#1", text="x"), EvidenceItem(chunk_id="docB:0:bbb#1", text="y")]
    assert _scrub_prose(
        "Liability is capped at 12 months of fees [docA:0:aaa#1], [docB:0:bbb#1]. Other evidence agrees.", ev
    ) == "Liability is capped at 12 months of fees. Other evidence agrees."
    assert _scrub_prose("Both agree [docA:0:aaa#1] , [docB:0:bbb#1] .", ev) == "Both agree."
    # a run of 3+ citations, and a trailing citation list at end-of-string
    ev3 = ev + [EvidenceItem(chunk_id="docC:0:ccc#1", text="z")]
    assert _scrub_prose("Capped [docA:0:aaa#1], [docB:0:bbb#1], [docC:0:ccc#1].", ev3) == "Capped."
    assert _scrub_prose("The cap is 12 months [docA:0:aaa#1], [docB:0:bbb#1]", ev) == "The cap is 12 months"
    # a non-comma clause separator is NOT citation-list residue and must survive untouched
    assert _scrub_prose(
        "Capped at 12 months [docA:0:aaa#1]; the term is annual [docB:0:bbb#1].", ev
    ) == "Capped at 12 months; the term is annual."


def test_scrub_preserves_legitimate_bracketed_quote_text():
    """Only the known annotation formats and the exact evidence ids are stripped -- a legitimately quoted
    bracket (e.g. a defined term '[Party A]') is not an annotation and must survive."""
    ev = [EvidenceItem(chunk_id="c1", text="[auto-tag: Definitions] \"[Party A]\" means Acme.")]
    dirty = GeneratedAnswer(
        answer='The agreement defines "[Party A]" as Acme. [auto-tag: Definitions]',
        citations=["c1"], abstained=False)
    result = generate_answer("Who is Party A?", ev, model=_StubModel(dirty))
    assert "[Party A]" in result.answer      # a real bracketed quote is kept
    assert "[auto-tag" not in result.answer  # the annotation is removed
    assert result.citations == ["c1"]


def test_answer_that_is_only_annotations_is_coerced_to_abstention():
    """If scrubbing an answer leaves no readable prose (it was nothing but annotations/ids), abstain rather
    than return an empty answer -- consistent with the no-claim-without-a-citation guarantee."""
    ev = [EvidenceItem(chunk_id="c1", text="[auto-tag: X] body")]
    dirty = GeneratedAnswer(answer="[auto-tag: X] [c1]", citations=["c1"], abstained=False)
    result = generate_answer("q", ev, model=_StubModel(dirty))
    assert result.abstained and result.citations == []


def test_model_abstention_is_respected():
    model = _StubModel(GeneratedAnswer(answer="Not stated.", citations=[], abstained=True))
    result = generate_answer("q", _EV, model=model)
    assert result.abstained and result.answer == "Not stated."


def test_confidence_is_surfaced_out_of_band_not_as_an_inline_marker():
    # engine issue 0002 (ADR-0055): confidence hedging is delivered as an out-of-band directive the prompt
    # consumes, NOT as a quotable [confidence: ...] marker in the evidence (which the model narrated ~100% of the
    # time it cited an uncertain clause). The raw enum token is never exposed, so it cannot be echoed; the
    # evidence block carries no confidence marker; but the hedging directive IS present so the answer still hedges.
    evidence = [EvidenceItem(chunk_id="c1", text="Acme affiliates Beta.", confidence="AMBIGUOUS")]
    model = _StubModel(GeneratedAnswer(answer="Acme affiliates Beta.", citations=["c1"], abstained=False))
    generate_answer("q", evidence, model=model)
    assert "[confidence:" not in _evidence_block(evidence)  # no inline marker in the evidence text
    assert "[confidence:" not in model.prompt and "AMBIGUOUS" not in model.prompt  # raw token never exposed
    assert "tentative" in model.prompt.lower()  # ...but the hedging directive is present


def test_typed_properties_are_never_rendered_into_the_prompt():
    # engine issue 0011 (ADR-0064): typed properties ride out-of-band on EvidenceItem.properties. The evidence
    # block -- and thus the prompt the model reads and may quote -- carries ONLY [chunk_id] text, never the
    # structured facts, so the "typed property cap_quantum=..." paraphrase leak is structurally impossible.
    evidence = [EvidenceItem(chunk_id="c1", text="Supplier's liability is capped at the fees paid.",
                             properties=[{"dimension": "cap_quantum", "value": "12_months"}])]
    model = _StubModel(GeneratedAnswer(answer="Capped at the fees paid.", citations=["c1"], abstained=False))
    generate_answer("q", evidence, model=model)
    block = _evidence_block(evidence)
    assert "cap_quantum" not in block and "=" not in block  # structured facts never enter the evidence text
    assert "cap_quantum" not in model.prompt  # ...nor the prompt the model reads
    assert "[c1] Supplier's liability is capped at the fees paid." in block  # just cited text


def test_extracted_confidence_adds_no_hedging_directive():
    evidence = [EvidenceItem(chunk_id="c1", text="Acme affiliates Beta.", confidence="EXTRACTED")]
    model = _StubModel(GeneratedAnswer(answer="x", citations=["c1"], abstained=False))
    generate_answer("q", evidence, model=model)
    assert "Certainty note" not in model.prompt  # a fully-certain fact needs no hedging directive


def test_prose_scrubbed_of_literal_schema_field_names():
    # engine issue 0002 follow-on: the model sometimes writes a literal schema FIELD NAME ("[chunk_id]") where a
    # citation would go -- engine vocabulary, not an id. Strip the bracketed field-name tokens from the prose.
    ev = [EvidenceItem(chunk_id="c1", text="Acme affiliates Beta.")]
    dirty = GeneratedAnswer(answer="The cap is expressed as [chunk_id] and applies per [clause_id].",
                            citations=["c1"], abstained=False)
    result = generate_answer("q", ev, model=_StubModel(dirty))
    assert "[chunk_id]" not in result.answer and "[clause_id]" not in result.answer
    assert result.citations == ["c1"]


def test_registers_under_fr_c_9():
    registry = CapabilityRegistry()
    register_generation(registry)
    reg = registry.get("generation")
    assert reg.name == "generation"
    assert reg.contract is GeneratedAnswer
    assert reg.kind == "agent_skill"  # CAP-REG-1: a single grounded/cited LLM act


def test_generation_method_loads_the_skill_body_without_frontmatter():
    # prompt-parity: the generation instruction is authored in skills/generation/SKILL.md, not hardcoded
    from rag_wright.capabilities.answer_generator import generation_method

    method = generation_method()
    assert method and not method.startswith("---")  # YAML frontmatter stripped
    assert "abstain" in method.lower() and "citation" in method.lower()  # the method's load-bearing rules


# --- strategy B: reason -> emit split (generation robustness) ------------------------------------


class _StubReason:
    def __init__(self, text: str = "analysis") -> None:
        self.text = text
        self.prompt: str | None = None

    def reason(self, prompt: str) -> str:
        self.prompt = prompt
        return self.text


class _RaisingReason:
    def reason(self, prompt: str) -> str:
        raise AssertionError("the reason model must not be called when there is no evidence")


def test_reasoned_split_reasons_then_emits_threading_the_analysis():
    reason = _StubReason("The evidence supports X via [c1].")
    emit = _StubModel(GeneratedAnswer(answer="X.", citations=["c1"], abstained=False))
    result = generate_answer_reasoned("q", _EV, reason_model=reason, emit_model=emit)
    assert result.answer == "X." and result.citations == ["c1"] and not result.abstained
    assert reason.prompt is not None  # step 1 (free-text reasoning) ran
    assert "STEP 2" in emit.prompt and "The evidence supports X via [c1]." in emit.prompt  # threaded in


def test_reasoned_split_empty_evidence_abstains_without_any_model_call():
    result = generate_answer_reasoned("q", [], reason_model=_RaisingReason(), emit_model=_RaisingModel())
    assert result.abstained and result.citations == []


def test_reasoned_split_applies_the_finalize_guarantees():
    reason = _StubReason()
    emit = _StubModel(GeneratedAnswer(answer="X.", citations=["c1", "c99"], abstained=False))
    result = generate_answer_reasoned("q", _EV, reason_model=reason, emit_model=emit)
    assert result.citations == ["c1"]  # fabricated c99 dropped by _finalize, same as the baseline


# --- strategy C: best-of-N self-consistency (generation robustness) -------------------------------

_EV2 = [
    EvidenceItem(chunk_id="c1", text="Acme and Beta are the parties."),
    EvidenceItem(chunk_id="c2", text="Governed by Delaware law."),
]


class _SequenceModel:
    """Hands out the given answers across calls, thread-safe (best-of-N runs the samples concurrently).
    Order of assignment is irrelevant: aggregation is over the MULTISET of samples."""

    def __init__(self, answers: list[GeneratedAnswer]) -> None:
        self._answers = list(answers)
        self._i = 0
        self._lock = threading.Lock()
        self.calls = 0

    def generate(self, prompt: str) -> GeneratedAnswer:
        with self._lock:
            answer = self._answers[self._i]
            self._i += 1
            self.calls += 1
        return answer


def test_best_of_n_picks_the_best_cited_non_abstaining_sample():
    answers = [
        GeneratedAnswer(answer="A", citations=[], abstained=True),
        GeneratedAnswer(answer="short", citations=["c1"], abstained=False),
        GeneratedAnswer(answer="rich", citations=["c1", "c2"], abstained=False),
        GeneratedAnswer(answer="A", citations=[], abstained=True),
        GeneratedAnswer(answer="A", citations=[], abstained=True),
    ]
    model = _SequenceModel(answers)
    result = generate_answer_best_of_n("q", _EV2, model=model, n=5)
    assert not result.abstained and result.answer == "rich" and set(result.citations) == {"c1", "c2"}
    assert model.calls == 5  # all n samples were drawn


def test_best_of_n_abstains_only_when_every_sample_abstains():
    answers = [GeneratedAnswer(answer="", citations=[], abstained=True)] * 4
    result = generate_answer_best_of_n("q", _EV2, model=_SequenceModel(answers), n=4)
    assert result.abstained  # unanimous abstain -> abstain


def test_best_of_n_min_answers_demands_agreement():
    answers = [
        GeneratedAnswer(answer="only", citations=["c1"], abstained=False),
        GeneratedAnswer(answer="", citations=[], abstained=True),
        GeneratedAnswer(answer="", citations=[], abstained=True),
    ]
    # a single non-abstaining sample is not enough when min_answers=2 (self-consistency threshold)
    result = generate_answer_best_of_n("q", _EV2, model=_SequenceModel(answers), n=3, min_answers=2)
    assert result.abstained


def test_best_of_n_empty_evidence_abstains_without_model_call():
    result = generate_answer_best_of_n("q", [], model=_RaisingModel(), n=3)
    assert result.abstained and result.citations == []


# --- client-side free-text + tag parse (self-hosted Gemma, no server guided decoding) -------------

_C1 = "CHANGEPOINT_2000-EX-10.6:418:428f0243bf74cabec"  # evidence-shaped chunk_ids
_C2 = "CHANGEPOINT_2000-EX-10.6:557:00a457517ca0dab3"


def test_parse_tagged_answer_wellformed():
    from rag_wright.capabilities.answer_generator import parse_tagged_answer

    text = (f"<answer>\nLiability is capped at fees paid [{_C1}], except uncapped for confidentiality "
            f"[{_C2}].\n</answer>\n<citations>\n{_C1}\n{_C2}\n</citations>")
    ans = parse_tagged_answer(text)
    assert not ans.abstained
    assert "Liability is capped" in ans.answer and ans.answer.endswith(".")
    assert ans.citations == [_C1, _C2]  # from the block


def test_parse_tagged_answer_missing_block_falls_back_to_inline():
    from rag_wright.capabilities.answer_generator import parse_tagged_answer

    ans = parse_tagged_answer(f"<answer>Governed by Texas law [{_C1}].</answer>")  # no <citations> block
    assert not ans.abstained and ans.citations == [_C1]  # recovered the inline [chunk_id]


def test_parse_tagged_answer_no_tags_uses_whole_text_and_inline_cites():
    from rag_wright.capabilities.answer_generator import parse_tagged_answer

    ans = parse_tagged_answer(f"The cap is 1x fees [{_C1}] and it excludes indirect damages.")  # bare prose
    assert not ans.abstained and _C1 in ans.citations and ans.answer.startswith("The cap")


def test_parse_tagged_answer_abstain_marker():
    from rag_wright.capabilities.answer_generator import parse_tagged_answer

    assert parse_tagged_answer("<abstain/>").abstained
    assert parse_tagged_answer("  ").abstained  # empty -> abstain


def test_parse_tagged_answer_dedupes_block_and_inline():
    from rag_wright.capabilities.answer_generator import parse_tagged_answer

    ans = parse_tagged_answer(f"<answer>x [{_C1}]</answer><citations>{_C1}</citations>")
    assert ans.citations == [_C1]  # not duplicated across block + inline


def test_tagged_freetext_model_generates_and_parses(monkeypatch):
    from rag_wright.capabilities import answer_generator as ag

    seen = {}

    class _Msg:
        content = f"<answer>Capped at fees [{_C1}].</answer>\n<citations>{_C1}</citations>"

    class _Runnable:
        def invoke(self, prompt):
            seen["prompt"] = prompt
            return _Msg()

    # build_model returns a PLAIN client (no with_structured_output -> no server guided decoding)
    monkeypatch.setattr(ag, "build_model", lambda mid, **kw: _Runnable())
    model = ag.TaggedFreeTextAnswerModel("google/gemma-4-31B-it-qat-w4a16-ct")
    ans = model.generate("PROMPT")
    assert not ans.abstained and ans.citations == [_C1]
    assert "EXACTLY these tags" in seen["prompt"]  # the tag-format instructions were appended


def test_answer_model_for_is_universally_tag_parse():
    # ADR-0045: generation is ALWAYS the client-side tag-parse path, for every model (LLM-agnostic).
    from rag_wright.capabilities.answer_generator import TaggedFreeTextAnswerModel, answer_model_for

    for mid in ("google/gemma-4-31B-it-qat-w4a16-ct", "ibm-granite/granite-4.2-8b", "google/gemma-4-31b-it"):
        assert isinstance(answer_model_for(mid), TaggedFreeTextAnswerModel)


# --- vision-to-text ------------------------------------------------------------------------------


class _StubVision:
    def __init__(self) -> None:
        self.seen: dict | None = None

    def image_to_text(self, image: bytes, *, media_type: str) -> str:
        self.seen = {"len": len(image), "media_type": media_type}
        return "transcribed text"


def test_vision_to_text_calls_the_vision_model():
    vision = _StubVision()
    out = vision_to_text(b"\x89PNGfake", model=vision, media_type="image/png")
    assert out == "transcribed text"
    assert vision.seen == {"len": len(b"\x89PNGfake"), "media_type": "image/png"}


def test_vision_to_text_registers_as_an_agent_skill():
    # SKILL-SPLIT: a single grounded vision-language act (like generation), not a function
    from rag_wright.capabilities.vision_to_text import VisionTranscription, register_vision_to_text

    registry = CapabilityRegistry()
    register_vision_to_text(registry)
    reg = registry.get("vision_to_text")
    assert reg.name == "vision_to_text"  # split from generation (ADR-0014)
    assert reg.contract is VisionTranscription
    assert reg.kind == "agent_skill"


def test_transcription_method_loads_the_skill_body_without_frontmatter():
    from rag_wright.capabilities.vision_to_text import transcription_method

    method = transcription_method()
    assert method and not method.startswith("---")  # YAML frontmatter stripped
    assert "reading order" in method.lower()  # the method is authored in the skill


# --- live Gemma (opt-in): real generation + real image transcription -----------------------------


@pytest.mark.model
def test_live_generation_grounds_and_cites_or_abstains():
    from rag_wright.capabilities.answer_generator import SeamAnswerModel

    evidence = [
        EvidenceItem(chunk_id="docA:0:h", text="This Agreement is between Acme Corporation and Beta LLC."),
        EvidenceItem(chunk_id="docA:1:h", text="This Agreement is governed by the laws of Delaware."),
    ]
    result = generate_answer("Which state's law governs this agreement?", evidence, model=SeamAnswerModel())

    if not result.abstained:
        assert result.citations  # a grounded claim is cited
        assert set(result.citations) <= {"docA:0:h", "docA:1:h"}  # only real evidence ids
        assert result.answer.strip()


@pytest.mark.model
def test_live_vision_to_text_transcribes_a_synthetic_image():
    from PIL import Image, ImageDraw

    from rag_wright.capabilities.vision_to_text import SeamVisionModel

    image = Image.new("RGB", (320, 90), "white")
    ImageDraw.Draw(image).text((10, 35), "HELLO WORLD", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    text = vision_to_text(buffer.getvalue(), model=SeamVisionModel(), media_type="image/png")
    assert "HELLO" in text.upper() or "WORLD" in text.upper()  # the model read the rendered text


# --- PREC-1a: structured sufficiency signal (answer_kind: answered | partial | abstained) -------------------


def test_answer_kind_backward_compat_derives_from_abstained():
    from rag_wright.capabilities.answer_generator import AnswerKind, GeneratedAnswer
    # existing construction (abstained only, no answer_kind) still works and derives the kind
    assert GeneratedAnswer(answer="a", citations=["c"], abstained=False).answer_kind is AnswerKind.ANSWERED
    assert GeneratedAnswer(answer="a", citations=[], abstained=True).answer_kind is AnswerKind.ABSTAINED


def test_answer_kind_partial_forces_abstained_false():
    from rag_wright.capabilities.answer_generator import AnswerKind, GeneratedAnswer
    g = GeneratedAnswer(answer="found related but not the exact term", citations=["c"],
                        abstained=False, answer_kind=AnswerKind.PARTIAL)
    assert g.answer_kind is AnswerKind.PARTIAL and g.abstained is False


def test_answer_kind_abstained_forces_abstained_true():
    from rag_wright.capabilities.answer_generator import AnswerKind, GeneratedAnswer
    # answer_kind wins when explicitly set: ABSTAINED -> abstained True even if passed False
    assert GeneratedAnswer(answer="x", citations=[], abstained=False,
                           answer_kind=AnswerKind.ABSTAINED).abstained is True


def test_parse_tagged_answer_marks_partial():
    from rag_wright.capabilities.answer_generator import AnswerKind, parse_tagged_answer
    g = parse_tagged_answer(
        "<answer>Mentions a related policy [X:1:aa11bbbb2222] but does not state the requirement.</answer>\n"
        "<partial/>\n<citations>X:1:aa11bbbb2222</citations>")
    assert g.answer_kind is AnswerKind.PARTIAL and not g.abstained and g.citations == ["X:1:aa11bbbb2222"]
    assert "<partial" not in g.answer  # the marker never leaks into the answer body


def test_parse_tagged_answer_default_kinds():
    from rag_wright.capabilities.answer_generator import AnswerKind, parse_tagged_answer
    assert parse_tagged_answer("<abstain/>").answer_kind is AnswerKind.ABSTAINED
    g = parse_tagged_answer("<answer>Capped at 2x [X:1:aa11bbbb2222].</answer><citations>X:1:aa11bbbb2222</citations>")
    assert g.answer_kind is AnswerKind.ANSWERED


def test_finalize_propagates_partial_and_coerces_uncited_partial_to_abstain():
    from rag_wright.capabilities.answer_generator import AnswerKind, EvidenceItem, GeneratedAnswer, _finalize
    ev = [EvidenceItem(chunk_id="X:1:aa11bbbb2222", text="a related mention")]
    raw = GeneratedAnswer(answer="related [X:1:aa11bbbb2222]", citations=["X:1:aa11bbbb2222"],
                          abstained=False, answer_kind=AnswerKind.PARTIAL)
    out = _finalize(raw, ev)
    assert out.answer_kind is AnswerKind.PARTIAL and not out.abstained
    # a PARTIAL that cites only ids NOT in the evidence has no valid citation -> coerced to abstain
    raw2 = GeneratedAnswer(answer="related [Y:9:ffff0000a]", citations=["Y:9:ffff0000a"],
                           abstained=False, answer_kind=AnswerKind.PARTIAL)
    assert _finalize(raw2, ev).answer_kind is AnswerKind.ABSTAINED
