"""Engine issue 0023: the span-relevance judge (span x condition -> verdict). Hermetic -- the structured seam is
stubbed, no LLM."""

from __future__ import annotations

import asyncio

from rag_wright.capabilities.span_relevance_judgment import (
    Condition,
    Relevance,
    RelevanceVerdict,
    ajudge_spans,
    build_arelevance_judge_fn,
    finalize_verdict,
    relevance_method,
    to_relevance,
)


# --- verdict vocab + conservative default --------------------------------------------------------


def test_to_relevance_maps_the_closed_vocab_and_defaults_unreadable_to_uncertain():
    assert to_relevance("relevant") is Relevance.RELEVANT
    assert to_relevance("not_relevant") is Relevance.NOT_RELEVANT
    assert to_relevance("not-relevant") is Relevance.NOT_RELEVANT   # dash normalized
    assert to_relevance("uncertain") is Relevance.UNCERTAIN
    assert to_relevance("probably yes") is Relevance.UNCERTAIN      # off-vocab -> conservative uncertain
    assert to_relevance("") is Relevance.UNCERTAIN


def test_finalize_verdict_conservative_default_and_confidence_clamp():
    assert finalize_verdict(None).verdict == "uncertain"           # judge failure -> uncertain (recall-safe)
    v = finalize_verdict(RelevanceVerdict(verdict="RELEVANT", rationale="x", confidence=2.0))
    assert v.verdict == "relevant" and v.confidence == 1.0         # normalized + clamped
    v2 = finalize_verdict(RelevanceVerdict(verdict="nonsense", rationale="", confidence=-1.0))
    assert v2.verdict == "uncertain" and v2.confidence == 0.0


# --- the judge fn wires the condition + span + matched context into the prompt -------------------


def _stub_factory(verdict: RelevanceVerdict, sink: dict):
    class _R:
        async def ainvoke(self, prompt):
            sink["prompt"] = prompt
            return verdict

    return lambda _m, _s, **_kw: _R()


async def test_judge_prompt_carries_condition_span_and_matched_as_context():
    sink: dict = {}
    judge = build_arelevance_judge_fn(
        "m", structured_factory=_stub_factory(RelevanceVerdict(verdict="relevant"), sink))
    cond = Condition(category="Cap On Liability", value_condition="multiple of fees",
                     question="how is liability capped")
    out = await judge("Supplier's liability shall not exceed the fees paid.", [("cap_basis", "multiple_of_fees")], cond)
    assert out.verdict == "relevant"
    p = sink["prompt"]
    assert "Cap On Liability" in p and "multiple of fees" in p            # the condition (category + value)
    assert "how is liability capped" in p                                  # question as context
    assert "cap_basis = multiple_of_fees" in p                             # matched[] as context
    assert "shall not exceed the fees paid" in p                          # the span text
    assert "EVIDENCE, NOT proof" in p or "not proof" in p.lower()         # framed as evidence, not a verdict


def test_relevance_method_loads_the_skill_body():
    body = relevance_method().lower()
    assert "relevant" in body and "not_relevant" in body and "uncertain" in body


def test_registers_as_a_canonical_agent_skill():
    # issue 0023/ADR-0088: promoted to a canonical FR-C slug -> registration is accepted and is an agent_skill
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.capabilities.span_relevance_judgment import register_span_relevance_judgment

    reg = CapabilityRegistry()
    register_span_relevance_judgment(reg)
    cap = reg.get("span_relevance_judgment")
    assert cap.kind == "agent_skill" and cap.contract is RelevanceVerdict


# --- ajudge_spans: concurrent, order-preserved, per-span timeout -> uncertain ---------------------


async def test_ajudge_spans_preserves_order_and_judges_every_span():
    async def ajudge(text, matched, condition):
        return RelevanceVerdict(verdict="relevant" if text == "a" else "not_relevant")

    cond = Condition(category="X")
    out = await ajudge_spans([("a", []), ("b", [])], cond, ajudge_fn=ajudge)
    assert [v.verdict for v in out] == ["relevant", "not_relevant"]      # 1:1, order preserved


async def test_ajudge_spans_timeout_becomes_uncertain_not_a_hang():
    async def slow(text, matched, condition):
        await asyncio.sleep(1.5)
        return RelevanceVerdict(verdict="relevant")

    cond = Condition(category="X")
    out = await ajudge_spans([("a", [])], cond, ajudge_fn=slow, timeout_s=0.05, timeout_retries=0)
    assert out[0].verdict == "uncertain"                                  # stalled provider -> conservative, no hang


async def test_ajudge_spans_bounds_wallclock_by_concurrency():
    # 4 spans each ~0.2s at concurrency 4 finish in ~0.2s, not ~0.8s serial (the parallel-LLM rule)
    async def slowish(text, matched, condition):
        await asyncio.sleep(0.2)
        return RelevanceVerdict(verdict="relevant")

    cond = Condition(category="X")
    t0 = asyncio.get_event_loop().time()
    out = await ajudge_spans([("a", []), ("b", []), ("c", []), ("d", [])], cond,
                             ajudge_fn=slowish, max_concurrency=4, timeout_s=None)
    assert len(out) == 4 and (asyncio.get_event_loop().time() - t0) < 0.5  # concurrent, not serial


# --- PS-4: query-side structured output is client-side tag parsing (ADR-0045), and an unreadable judgment degrades --


def test_the_judge_uses_client_side_tag_parsing_by_default():
    import inspect

    from rag_wright.models.tag_structured import build_tag_structured

    default = inspect.signature(build_arelevance_judge_fn).parameters["structured_factory"].default
    assert default is build_tag_structured


async def test_an_unreadable_judgment_becomes_uncertain_not_an_error():
    class _Unreadable:
        async def ainvoke(self, prompt):
            RelevanceVerdict.model_validate({})  # the model's answer never parsed (raises ValidationError)

    judge = build_arelevance_judge_fn("m", structured_factory=lambda _m, _s, **_kw: _Unreadable())
    out = await judge("some span", [], Condition(category="payment terms"))
    assert finalize_verdict(out).verdict == "uncertain" and out.confidence == 0.0


async def test_a_non_parse_judge_error_still_propagates():
    import pytest

    class _Broken:
        async def ainvoke(self, prompt):
            raise RuntimeError("provider down")

    judge = build_arelevance_judge_fn("m", structured_factory=lambda _m, _s, **_kw: _Broken())
    with pytest.raises(RuntimeError):
        await judge("some span", [], Condition(category="payment terms"))


# --- PS-R5a: the method is domain-neutral; a domain appends its guidance -----------------------------------------


def test_the_engine_method_is_domain_neutral_and_a_domain_appends_its_guidance():
    method = relevance_method()
    assert "contract" not in method.lower() and "clause" not in method.lower()
    with_guidance = relevance_method("The documents are textile test reports.")
    assert with_guidance.startswith(method) and with_guidance.endswith("The documents are textile test reports.")
    assert relevance_method("   ") == method  # blank guidance adds nothing


async def test_the_judge_prompt_carries_the_domain_guidance():
    sink: dict = {}
    judge = build_arelevance_judge_fn("m", guidance="DOMAIN-HINT-42",
                                      structured_factory=_stub_factory(RelevanceVerdict(verdict="relevant"), sink))
    await judge("span", [], Condition(category="fabric strength"))
    assert "DOMAIN-HINT-42" in sink["prompt"] and "category: fabric strength" in sink["prompt"]


def test_the_reference_pack_supplies_its_contract_guidance():
    from rag_wright.packs.contracts.skills.guidance import contract_guidance

    assert "clause type" in contract_guidance("relevance") and "carve-outs" in contract_guidance("generation")
