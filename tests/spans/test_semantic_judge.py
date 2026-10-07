"""JUDGE-SEMANTIC (ADR-0040), SKILL-SPLIT: the narrowed semantic judge, split into the
`extraction_semantic_judge` SKILL (the verify-or-refute reading) and the `extraction_semantic_gate` FUNCTION
(the deterministic AMBIGUOUS downgrade). Hermetic -- fake judge, no model, no network."""

from __future__ import annotations

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import (
    CLOSED_VOCAB,
    ClausePropertyRecord,
    PropertyAssertion,
    PropertyDimension,
)
from rag_wright.contracts.provenance import ConfidenceTag, Provenance
from rag_wright.spans.property_grounding import GROUNDING_CUES
from rag_wright.spans.semantic_judge import (
    SEMANTIC_DIMENSIONS,
    SemanticVerdict,
    build_semantic_judge_fn,
    judgment_method,
    register_extraction_semantic_gate,
    register_extraction_semantic_judge,
    semantic_judge,
)

_D = PropertyDimension
_PROV = Provenance.of(ChunkId.of("doc", 0, "clause body"))


def _record(function: str, *assertions: tuple) -> ClausePropertyRecord:
    return ClausePropertyRecord(
        clause_id=str(_PROV.chunk_id), function=function,
        assertions=[PropertyAssertion(provenance=_PROV, confidence=c, dimension=d, value=v)
                    for d, v, c in assertions],
    )


def test_semantic_dimensions_are_closed_dims_with_no_lexical_cue():
    # the residual Layers 1-2 cannot reach: in CLOSED_VOCAB (not open-valued) AND not lexically anchored
    assert SEMANTIC_DIMENSIONS == {d for d in CLOSED_VOCAB if d not in GROUNDING_CUES}
    assert _D.MUTUALITY in SEMANTIC_DIMENSIONS and _D.CAP_BASIS in SEMANTIC_DIMENSIONS
    assert _D.CARVE_OUT not in SEMANTIC_DIMENSIONS  # lexically anchored -> grounding judge's job
    assert _D.JURISDICTION not in SEMANTIC_DIMENSIONS  # open-valued -> token check's job


def test_refuted_semantic_assertion_is_downgraded_supported_is_kept():
    # a fake judge that refutes mutuality=mutual (a one-sided clause) but supports party_asymmetry
    def judge(dim, value, text):
        return SemanticVerdict(supported=(dim is not _D.MUTUALITY))

    rec = semantic_judge(
        _record(
            "Cap On Liability",
            (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),  # refuted -> AMBIGUOUS
            (_D.PARTY_ASYMMETRY, "symmetric", ConfidenceTag.EXTRACTED),  # supported -> kept
        ),
        "only Licensee shall indemnify Licensor",
        judge,
    )
    by = {a.dimension: a.confidence for a in rec.assertions}
    assert by[_D.MUTUALITY] == ConfidenceTag.AMBIGUOUS
    assert by[_D.PARTY_ASYMMETRY] == ConfidenceTag.EXTRACTED


def test_only_surviving_semantic_assertions_are_judged():
    # a lexical dim and an already-AMBIGUOUS semantic dim must NOT be sent to the judge
    seen: list[tuple] = []

    def judge(dim, value, text):
        seen.append((dim, value))
        return SemanticVerdict(supported=True)

    semantic_judge(
        _record(
            "Cap On Liability",
            (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),  # semantic, alive -> judged
            (_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED),  # lexical -> NOT judged
            (_D.CAP_BASIS, "fixed_fee", ConfidenceTag.AMBIGUOUS),  # already AMBIGUOUS -> NOT judged
        ),
        "clause text",
        judge,
    )
    assert seen == [(_D.MUTUALITY, "mutual")]


def test_none_verdict_leaves_the_assertion_untouched():
    rec = semantic_judge(
        _record("Cap On Liability", (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED)),
        "clause text",
        lambda dim, value, text: None,  # judge could not rule
    )
    assert rec.assertions[0].confidence == ConfidenceTag.EXTRACTED


def test_no_semantic_assertions_is_a_noop():
    rec = _record("Uncapped Liability", (_D.CARVE_OUT, "fraud", ConfidenceTag.EXTRACTED))
    assert semantic_judge(rec, "text", lambda *a: SemanticVerdict(supported=False)) is rec


def test_build_semantic_judge_fn_invokes_the_injected_structured_factory():
    captured = {}

    class _FakeRunnable:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return SemanticVerdict(supported=False, reason="one-sided")

    def _factory(model_id, schema, **kw):
        captured["model_id"] = model_id
        captured["schema"] = schema
        captured["label"] = kw.get("label")
        return _FakeRunnable()

    judge = build_semantic_judge_fn("ibm-granite/granite-4.2-8b", structured_factory=_factory)
    verdict = judge(_D.MUTUALITY, "mutual", "only Licensee shall indemnify")
    assert verdict == SemanticVerdict(supported=False, reason="one-sided")
    assert captured["label"] == "semantic_judge.judge"  # issue 0005: the judge names its stage
    assert captured["model_id"] == "ibm-granite/granite-4.2-8b"
    assert captured["schema"] is SemanticVerdict
    assert "mutual" in captured["prompt"] and "only Licensee shall indemnify" in captured["prompt"]
    # the prompt is the authored SKILL method + the per-call tail (not a hardcoded string)
    assert captured["prompt"].startswith(judgment_method())


# --- SKILL-SPLIT: the SKILL loads, and the two capabilities register with the right kinds ---------


def test_judgment_method_loads_the_skill_body_without_frontmatter():
    method = judgment_method()
    assert method and not method.startswith("---")  # YAML frontmatter stripped
    assert "supported=true" in method  # the strictness rule is authored in the skill


def test_registers_the_skill_and_the_gate_split():
    # SKILL-SPLIT: the LLM verify-or-refute reading is an agent_skill; the AMBIGUOUS-downgrade gate is a function
    reg = CapabilityRegistry()
    register_extraction_semantic_judge(reg)
    register_extraction_semantic_gate(reg)
    skill = reg.get("extraction_semantic_judge")
    assert skill.kind == "agent_skill" and skill.contract is SemanticVerdict
    gate = reg.get("extraction_semantic_gate")
    assert gate.kind == "function" and gate.contract is ClausePropertyRecord


# --- ING-9: the DECISION-MODEL (Jev) judge -- one batched call per provision, calibrated scores ------------------

def test_the_decision_judge_request_states_the_rule_once_then_the_clause_and_each_property():
    from rag_wright.spans.semantic_judge import judge_request

    state, questions = judge_request("Only  Licensee\nshall indemnify.", [(_D.MUTUALITY, "mutual"),
                                                                         (_D.PARTY_ASYMMETRY, "symmetric")])
    rule, rest = state.split("\n\nClause:\n", 1)
    assert "genuinely SUPPORTS" in rule and "silent" in rule  # the SKILL's strictness, stated once
    clause, props = rest.split("\n\nProperties:\n", 1)
    assert clause == "Only Licensee shall indemnify."
    assert props.splitlines()[0].startswith("[0] mutuality = mutual (meaning: ")
    assert list(questions) == ["p0", "p1"] and questions["p1"]["type"] == "noul"
    assert questions["p0"]["criteria"] == questions["p1"]["criteria"]


def _decision_judge(monkeypatch, scores=None, error=None):
    import rag_wright.api as api
    from rag_wright.spans import semantic_judge as sj

    calls = []

    async def fake_invoke(name, inputs, resources=None):
        calls.append((name, inputs))
        if error:
            raise error
        return {"answers": {f"p{i}": {"noul": s} for i, s in enumerate(scores)}}

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(api, "ainvoke_model", fake_invoke)
    monkeypatch.setattr(api, "capability_index", lambda: {"jev_decision": {}})
    return sj.build_decision_judge(), calls


def test_the_decision_judge_rules_on_every_semantic_value_in_one_call(monkeypatch):
    import asyncio

    from rag_wright.spans.semantic_judge import asemantic_judge

    judge, calls = _decision_judge(monkeypatch, scores=[0.08, 0.93])
    rec = asyncio.run(asemantic_judge(_record(
        "Cap On Liability",
        (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED),             # 0.08 -> refuted -> AMBIGUOUS
        (_D.PARTY_ASYMMETRY, "symmetric", ConfidenceTag.INFERRED),     # 0.93 -> supported -> kept
        (_D.CAP_BASIS, "fixed_fee", ConfidenceTag.AMBIGUOUS),          # already AMBIGUOUS -> not judged
    ), "only Licensee shall indemnify Licensor", judge))
    assert len(calls) == 1 and calls[0][0] == "jev_decision" and len(calls[0][1]["questions"]) == 2
    by = {a.dimension: a.confidence for a in rec.assertions}
    assert by[_D.MUTUALITY] == ConfidenceTag.AMBIGUOUS and by[_D.PARTY_ASYMMETRY] == ConfidenceTag.INFERRED


def test_a_decision_judge_error_leaves_every_value_untouched(monkeypatch):
    import asyncio

    from rag_wright.spans.semantic_judge import asemantic_judge

    judge, _ = _decision_judge(monkeypatch, error=RuntimeError("decisions API down"))
    before = _record("Cap On Liability", (_D.MUTUALITY, "mutual", ConfidenceTag.EXTRACTED))
    assert asyncio.run(asemantic_judge(before, "text", judge)) == before


def test_the_default_judge_is_the_decision_model_when_one_is_configured(monkeypatch):
    from rag_wright.spans.semantic_judge import DecisionJudge, select_asemantic_judge

    _decision_judge(monkeypatch, scores=[])
    monkeypatch.delenv("RAG_SEMANTIC_JUDGE", raising=False)
    assert isinstance(select_asemantic_judge(None), DecisionJudge)
    assert not isinstance(select_asemantic_judge("some/llm"), DecisionJudge)   # an explicit model -> the LLM judge
    monkeypatch.setenv("RAG_SEMANTIC_JUDGE", "llm")
    assert not isinstance(select_asemantic_judge(None), DecisionJudge)         # forced LLM judge


def test_without_a_decision_model_the_judge_falls_back_to_the_llm_judge(monkeypatch):
    from rag_wright.spans.semantic_judge import DecisionJudge, select_asemantic_judge

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("RAG_SEMANTIC_JUDGE", raising=False)
    judge = select_asemantic_judge(None)
    assert judge is not None and not isinstance(judge, DecisionJudge)
