"""PS-4 (G19): a product composing its own question answering over engine retrieval generates a grounded, cited
answer and judges span relevance from `rag_wright.api`, the model resolved from the workspace's roles."""
from __future__ import annotations

import asyncio

from rag_wright.api import (AnswerKind, Condition, EngineConfig, EvidenceItem, GeneratedAnswer, ModelRole,
                            Relevance, RelevanceVerdict, StoreConfig, agenerate_answer, ajudge_spans)
from rag_wright.api.workspace import WorkspaceHandle

_CID = "doc_md:0:0123456789abcdef"


def _ws(**models):
    return WorkspaceHandle(None, EngineConfig(store=StoreConfig("localhost", 2480, "u", "p"), models=models), "t")


class _StubAnswerModel:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    async def agenerate(self, prompt):
        self.prompts.append(prompt)
        return self.answer


def _patch_answer_model(monkeypatch, answer):
    built = {}

    def _for(model_id=None, **kw):
        built["model_id"] = model_id
        built["model"] = _StubAnswerModel(answer)
        return built["model"]

    monkeypatch.setattr("rag_wright.capabilities.answer_generator.answer_model_for", _for)
    return built


def test_generate_uses_the_workspace_general_model_and_keeps_valid_citations(monkeypatch):
    built = _patch_answer_model(monkeypatch, GeneratedAnswer(answer=f"Net 30 [{_CID}]", citations=[_CID]))
    ws = _ws(**{ModelRole.GENERAL.value: "my/general-model"})
    out = asyncio.run(agenerate_answer("When is payment due?", [EvidenceItem(chunk_id=_CID, text="Pay in 30 days.")],
                                       ws=ws))
    assert built["model_id"] == "my/general-model"
    assert out.citations == [_CID] and out.answer_kind is AnswerKind.ANSWERED
    assert "When is payment due?" in built["model"].prompts[0]


def test_generate_drops_citations_outside_the_evidence_and_abstains(monkeypatch):
    _patch_answer_model(monkeypatch, GeneratedAnswer(answer="made up [x:1:deadbeefdeadbeef]",
                                                     citations=["x:1:deadbeefdeadbeef"]))
    out = asyncio.run(agenerate_answer("q", [EvidenceItem(chunk_id=_CID, text="t")], ws=_ws()))
    assert out.abstained and out.citations == []


def test_generate_abstains_on_empty_evidence_without_a_model_call(monkeypatch):
    built = _patch_answer_model(monkeypatch, None)
    out = asyncio.run(agenerate_answer("q", [], ws=_ws()))
    assert out.answer_kind is AnswerKind.ABSTAINED and built["model"].prompts == []


def test_judge_uses_the_workspace_structured_model_and_returns_final_verdicts(monkeypatch):
    seen = {}

    def _build(model_id, **kw):
        seen["model_id"] = model_id

        async def judge(span_text, matched, condition):
            seen.setdefault("calls", []).append((span_text, matched, condition.category))
            return RelevanceVerdict(verdict={"a": "Relevant", "b": "gibberish"}[span_text], confidence=1.7)

        return judge

    monkeypatch.setattr("rag_wright.capabilities.span_relevance_judgment.build_arelevance_judge_fn", _build)
    ws = _ws(**{ModelRole.STRUCTURED_REASONING.value: "my/judge-model"})
    out = asyncio.run(ajudge_spans([("a", [("term", "30 days")]), ("b", [])], Condition(category="payment"),
                                   ws=ws))
    assert seen["model_id"] == "my/judge-model"
    assert [v.verdict for v in out] == [Relevance.RELEVANT.value, Relevance.UNCERTAIN.value]  # unreadable -> uncertain
    assert all(v.confidence == 1.0 for v in out)
    assert seen["calls"][0] == ("a", [("term", "30 days")], "payment")


def test_the_exports_are_public():
    from rag_wright import api

    assert {"agenerate_answer", "ajudge_spans", "EvidenceItem", "GeneratedAnswer", "AnswerKind", "RelevanceVerdict",
            "Relevance", "Condition"} <= set(api.__all__)


def test_generation_guidance_reaches_the_prompt(monkeypatch):
    built = _patch_answer_model(monkeypatch, GeneratedAnswer(answer=f"x [{_CID}]", citations=[_CID]))
    asyncio.run(agenerate_answer("q", [EvidenceItem(chunk_id=_CID, text="t")], ws=_ws(), guidance="DOMAIN-HINT-7"))
    prompt = built["model"].prompts[0]
    assert "## Domain guidance" in prompt and "DOMAIN-HINT-7" in prompt


def test_the_generation_method_is_domain_neutral():
    from rag_wright.capabilities.answer_generator import generation_method

    text = generation_method().lower()
    assert "contract" not in text and "liability" not in text and "clause" not in text
