"""LG-3b: the `intra_document_qa` composite subgraph -- hermetic (stub serve/clause_text/generate, no LLM/DB).

Composes the intra-contract scoped KG query (contract_kg_serve) -> rehydrate each clause's REAL operative-span
text -> generate_answer, as one hardened LangGraph. Query-side posture: a transient serve failure degrades to
empty evidence (the generator abstains), never a dropped item; an orphaned span_id (a real pipeline
inconsistency) dead-letters rather than fabricating. The evidence is the real clause language cited by
`clause_id`, with typed facts appended and worst-case confidence surfaced (FR-S.4).
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.capabilities.answer_generator import GeneratedAnswer
from rag_wright.capabilities.contract_kg_serve import CitedClause, CitedProperty
from rag_wright.subgraphs.intra_document_qa import build_intra_document_qa

_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)


def _clause(clause_id, function, props):
    """props: list of (dimension, value, confidence, span_id)."""
    return CitedClause(
        contract_id=clause_id.split(":", 1)[0], clause_id=clause_id, function=function,
        properties=[CitedProperty(dimension=d, value=v, edge_type="HAS", confidence=c, span_id=s)
                    for d, v, c, s in props],
    )


class _StubServe:
    def __init__(self, clauses, fail_times=0):
        self._clauses = clauses
        self._fail_times = fail_times
        self.calls = 0

    def __call__(self, contract_id, question):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("store blip")
        return self._clauses


def _clause_text(mapping):
    def fn(contract_id, clauses):
        return dict(mapping)
    return fn


def _clause_text_orphan(contract_id, clauses):
    raise KeyError("clause k:0:h: span 's0' has no text in contract k")


def _capturing_generate():
    seen = {}

    def generate(question, evidence):
        seen["evidence"] = evidence
        if not evidence:
            return GeneratedAnswer(answer="abstain", citations=[], abstained=True)
        return GeneratedAnswer(
            answer=f"answer to {question}", citations=[e.chunk_id for e in evidence], abstained=False
        )

    return generate, seen


def test_composes_serve_rehydrate_generate_into_a_cited_answer():
    clauses = [_clause("k:0:h", "Cap On Liability", [("cap_scope", "mutual", "EXTRACTED", "s0")])]
    serve = _StubServe(clauses)
    generate, _ = _capturing_generate()
    graph = build_intra_document_qa(
        serve, _clause_text({"k:0:h": "Total liability shall not exceed the fees paid."}), generate,
        retry_policy=_FAST_RETRY)

    out = graph.invoke({"contract_id": "k", "question": "What is the liability cap?"})

    assert out["answer"].abstained is False
    assert out["answer"].citations == ["k:0:h"]  # cited by clause_id (no claim without a citation)
    assert serve.calls == 1


def test_real_clause_text_and_typed_facts_and_worst_case_confidence_in_evidence():
    clauses = [_clause("k:0:h", "Indemnity",
                       [("covers", "fraud", "EXTRACTED", "s0"), ("mutuality", "one-way", "AMBIGUOUS", "s0")])]
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(
        _StubServe(clauses), _clause_text({"k:0:h": "Each party shall indemnify the other for fraud."}),
        generate, retry_policy=_FAST_RETRY)

    graph.invoke({"contract_id": "k", "question": "q"})

    item = seen["evidence"][0]
    assert "Each party shall indemnify the other for fraud." in item.text  # the REAL clause language
    assert "covers=fraud" in item.text  # typed facts appended
    assert item.confidence == "AMBIGUOUS"  # worst-case provenance surfaced (FR-S.4)


def test_property_less_clause_cites_its_function_label():
    clauses = [_clause("k:0:h", "Governing Law", [])]  # no properties -> no span text
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(_StubServe(clauses), _clause_text({}), generate, retry_policy=_FAST_RETRY)

    out = graph.invoke({"contract_id": "k", "question": "q"})

    assert seen["evidence"][0].text == "Governing Law"  # falls back to the function label, no crash
    assert out["answer"].citations == ["k:0:h"]


def test_orphan_span_dead_letters_without_fabricating_or_crashing():
    clauses = [_clause("k:0:h", "Cap On Liability", [("cap_scope", "mutual", "EXTRACTED", "s0")])]
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(_StubServe(clauses), _clause_text_orphan, generate, retry_policy=_FAST_RETRY)

    out = graph.invoke({"contract_id": "k", "question": "q"})

    assert out["dead_letter"]["reason"] == "clause_text_orphan_span"  # surfaced, not fabricated
    assert "answer" not in out  # generate skipped
    assert "evidence" not in seen


def test_no_matching_clauses_abstains_without_fabrication():
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(_StubServe([]), _clause_text({}), generate, retry_policy=_FAST_RETRY)

    out = graph.invoke({"contract_id": "k", "question": "q"})

    assert out["answer"].abstained is True
    assert seen["evidence"] == []


def test_transient_serve_retries_then_degrades_to_empty_and_abstains():
    clauses = [_clause("k:0:h", "Cap On Liability", [])]
    serve = _StubServe(clauses, fail_times=99)  # always fails
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(serve, _clause_text({}), generate, retry_policy=_FAST_RETRY)

    out = graph.invoke({"contract_id": "k", "question": "q"})

    assert serve.calls == 3  # retried up to max_attempts
    assert out["answer"].abstained is True  # degraded to empty -> abstain (query survives)
    assert seen["evidence"] == []


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.intra_document_qa import register_intra_document_qa

    reg = CapabilityRegistry()
    register_intra_document_qa(reg)
    assert reg.get("intra_document_qa").kind == "subgraph"
    assert reg.get("intra_document_qa").contract is GeneratedAnswer


# --- rehydrate_clause_texts: the A1 fix (persist-clause-span-id) -- property-less clauses get REAL text --------


class _FakeSpanStore:
    """A minimal store for rehydrate_clause_texts: spans_by_contract filters by function (like ArcadeDBStore)."""

    def __init__(self, spans: list[dict]) -> None:
        self._spans = spans  # [{span_id, text, function}]

    def spans_by_contract(self, contract_id: str, functions: list[str]) -> list[dict]:
        return [s for s in self._spans if s["function"] in functions]


def test_property_less_clause_rehydrates_from_its_own_span_id_not_a_bare_label():
    from rag_wright.subgraphs.intra_document_qa import rehydrate_clause_texts

    spans = [{"span_id": "S#3", "text": "Liability is uncapped for IP indemnity.", "function": "Uncapped Liability"}]
    clause = CitedClause(contract_id="C", clause_id="C:3:h", function="Uncapped Liability",
                         span_id="S#3", properties=[])  # property-less, but carries its own span_id
    texts = rehydrate_clause_texts(_FakeSpanStore(spans), "C", [clause])
    assert texts["C:3:h"] == "Liability is uncapped for IP indemnity."  # REAL text, not the bare function label


def test_property_bearing_clause_uses_its_property_span_ids_over_the_clause_span_id():
    from rag_wright.subgraphs.intra_document_qa import rehydrate_clause_texts

    spans = [{"span_id": "S#1", "text": "Cap at the fees paid.", "function": "Cap On Liability"}]
    clause = CitedClause(
        contract_id="C", clause_id="C:1:h", function="Cap On Liability", span_id="S#9",  # clause span_id NOT used
        properties=[CitedProperty(dimension="cap_basis", value="multiple_of_fees", edge_type="HAS", span_id="S#1")])
    texts = rehydrate_clause_texts(_FakeSpanStore(spans), "C", [clause])
    assert texts["C:1:h"] == "Cap at the fees paid."  # the property span_id path (grounded provenance)


def test_legacy_clause_with_no_span_link_is_omitted_and_falls_back_to_the_label():
    from rag_wright.subgraphs.intra_document_qa import rehydrate_clause_texts

    clause = CitedClause(contract_id="C", clause_id="C:2:h", function="Governing Law", span_id="", properties=[])
    texts = rehydrate_clause_texts(_FakeSpanStore([]), "C", [clause])
    assert "C:2:h" not in texts  # no span link (pre-backfill) -> evidence builder cites the function label
