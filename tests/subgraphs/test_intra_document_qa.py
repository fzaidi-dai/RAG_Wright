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
from rag_wright.packs.contracts.capabilities.contract_kg_serve import CitedClause, CitedProperty
from rag_wright.packs.contracts.subgraphs.intra_document_qa import build_intra_document_qa

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

    async def generate(question, evidence):
        seen["evidence"] = evidence
        if not evidence:
            return GeneratedAnswer(answer="abstain", citations=[], abstained=True)
        return GeneratedAnswer(
            answer=f"answer to {question}", citations=[e.chunk_id for e in evidence], abstained=False
        )

    return generate, seen


async def test_composes_serve_rehydrate_generate_into_a_cited_answer():
    clauses = [_clause("k:0:h", "Cap On Liability", [("cap_scope", "mutual", "EXTRACTED", "s0")])]
    serve = _StubServe(clauses)
    generate, _ = _capturing_generate()
    graph = build_intra_document_qa(
        serve, _clause_text({"k:0:h": "Total liability shall not exceed the fees paid."}), generate,
        retry_policy=_FAST_RETRY)

    out = await graph.ainvoke({"contract_id": "k", "question": "What is the liability cap?"})

    assert out["answer"].abstained is False
    assert out["answer"].citations == ["k:0:h"]  # cited by clause_id (no claim without a citation)
    assert serve.calls == 1


async def test_real_clause_text_and_typed_facts_and_worst_case_confidence_in_evidence():
    clauses = [_clause("k:0:h", "Indemnity",
                       [("covers", "fraud", "EXTRACTED", "s0"), ("mutuality", "one-way", "AMBIGUOUS", "s0")])]
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(
        _StubServe(clauses), _clause_text({"k:0:h": "Each party shall indemnify the other for fraud."}),
        generate, retry_policy=_FAST_RETRY)

    await graph.ainvoke({"contract_id": "k", "question": "q"})

    item = seen["evidence"][0]
    assert item.text == "Each party shall indemnify the other for fraud."  # the REAL clause language, ONLY
    # engine issue 0011 / ADR-0064: typed facts are NO LONGER appended to the text (the model paraphrased them);
    # they ride out-of-band on .properties, so the generator never sees the schema tokens.
    assert "covers=fraud" not in item.text and "=" not in item.text
    assert item.properties == [{"dimension": "covers", "value": "fraud"},
                               {"dimension": "mutuality", "value": "one-way"}]
    assert item.confidence == "AMBIGUOUS"  # worst-case provenance surfaced (FR-S.4)


async def test_property_less_clause_with_no_text_is_dropped():
    # engine issue 0002 / ADR-0054: a clause with no span text AND no typed facts is contentless -- nothing to
    # ground a citation on -- so it is dropped from evidence (it previously cited a bare function label). With it
    # the only clause, the generator abstains.
    clauses = [_clause("k:0:h", "Governing Law", [])]  # no properties -> no span text
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(_StubServe(clauses), _clause_text({}), generate, retry_policy=_FAST_RETRY)

    out = await graph.ainvoke({"contract_id": "k", "question": "q"})

    assert seen["evidence"] == []  # contentless clause dropped, not cited by a bare label
    assert out["answer"].abstained and out["answer"].citations == []  # nothing to cite -> abstain


async def test_orphan_span_dead_letters_without_fabricating_or_crashing():
    clauses = [_clause("k:0:h", "Cap On Liability", [("cap_scope", "mutual", "EXTRACTED", "s0")])]
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(_StubServe(clauses), _clause_text_orphan, generate, retry_policy=_FAST_RETRY)

    out = await graph.ainvoke({"contract_id": "k", "question": "q"})

    assert out["dead_letter"]["reason"] == "clause_text_orphan_span"  # surfaced, not fabricated
    assert "answer" not in out  # generate skipped
    assert "evidence" not in seen


async def test_no_matching_clauses_abstains_without_fabrication():
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(_StubServe([]), _clause_text({}), generate, retry_policy=_FAST_RETRY)

    out = await graph.ainvoke({"contract_id": "k", "question": "q"})

    assert out["answer"].abstained is True
    assert seen["evidence"] == []


async def test_transient_serve_retries_then_degrades_to_empty_and_abstains():
    clauses = [_clause("k:0:h", "Cap On Liability", [])]
    serve = _StubServe(clauses, fail_times=99)  # always fails
    generate, seen = _capturing_generate()
    graph = build_intra_document_qa(serve, _clause_text({}), generate, retry_policy=_FAST_RETRY)

    out = await graph.ainvoke({"contract_id": "k", "question": "q"})

    assert serve.calls == 3  # retried up to max_attempts
    assert out["answer"].abstained is True  # degraded to empty -> abstain (query survives)
    assert seen["evidence"] == []


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import register_intra_document_qa

    reg = CapabilityRegistry()
    register_intra_document_qa(reg)
    assert reg.get("intra_document_qa").kind == "subgraph"
    assert reg.get("intra_document_qa").contract is GeneratedAnswer


# --- rehydrate_clause_texts: the A1 fix (persist-clause-span-id) -- property-less clauses get REAL text --------


class _FakeSpanStore:
    """A minimal store for rehydrate_clause_texts: spans_by_contract filters by function (like ArcadeDBStore)."""

    def __init__(self, spans: list[dict]) -> None:
        self._spans = spans  # [{span_id, text, function}]

    def spans_by_document(self, contract_id: str, functions: list[str]) -> list[dict]:
        return [s for s in self._spans if s["function"] in functions]


def test_property_less_clause_rehydrates_from_its_own_span_id_not_a_bare_label():
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import rehydrate_clause_texts

    spans = [{"span_id": "S#3", "text": "Liability is uncapped for IP indemnity.", "function": "Uncapped Liability"}]
    clause = CitedClause(contract_id="C", clause_id="C:3:h", function="Uncapped Liability",
                         span_id="S#3", properties=[])  # property-less, but carries its own span_id
    texts = rehydrate_clause_texts(_FakeSpanStore(spans), "C", [clause])
    assert texts["C:3:h"] == "Liability is uncapped for IP indemnity."  # REAL text, not the bare function label


def test_property_bearing_clause_uses_its_property_span_ids_over_the_clause_span_id():
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import rehydrate_clause_texts

    spans = [{"span_id": "S#1", "text": "Cap at the fees paid.", "function": "Cap On Liability"}]
    clause = CitedClause(
        contract_id="C", clause_id="C:1:h", function="Cap On Liability", span_id="S#9",  # clause span_id NOT used
        properties=[CitedProperty(dimension="cap_basis", value="multiple_of_fees", edge_type="HAS", span_id="S#1")])
    texts = rehydrate_clause_texts(_FakeSpanStore(spans), "C", [clause])
    assert texts["C:1:h"] == "Cap at the fees paid."  # the property span_id path (grounded provenance)


def test_legacy_clause_with_no_span_link_is_omitted_and_falls_back_to_the_label():
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import rehydrate_clause_texts

    clause = CitedClause(contract_id="C", clause_id="C:2:h", function="Governing Law", span_id="", properties=[])
    texts = rehydrate_clause_texts(_FakeSpanStore([]), "C", [clause])
    assert "C:2:h" not in texts  # no span link (pre-backfill) -> evidence builder cites the function label


# --- ADR-0044: query consumption of the IsExceptionTo carve-out relationship --------------------------------


def test_attach_exception_links_pulls_a_caps_carveouts_as_inferred_exceptions():
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import attach_exception_links

    cap = CitedClause(contract_id="C", clause_id="C:5:h", function="Cap On Liability", span_id="s5")
    seen = {}

    def exceptions_fn(cap_id):
        seen["cap_id"] = cap_id
        return [{"clause_id": "C:6:h", "function": "Uncapped Liability", "span_id": "s6"}]

    out = attach_exception_links([cap], exceptions_fn, contract_id="C")
    assert seen["cap_id"] == "C:5:h"
    exc = next(c for c in out if c.clause_id == "C:6:h")  # the exception was pulled in even though not classified
    assert exc.exception_of == "C:5:h" and exc.span_id == "s6"  # marked + rehydratable from its own span


def test_attach_marks_an_already_served_uncapped_clause_and_does_not_duplicate():
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import attach_exception_links

    cap = CitedClause(contract_id="C", clause_id="C:5:h", function="Cap On Liability", span_id="s5")
    unc = CitedClause(contract_id="C", clause_id="C:6:h", function="Uncapped Liability", span_id="s6")
    out = attach_exception_links(
        [cap, unc], lambda cid: [{"clause_id": "C:6:h", "function": "Uncapped Liability", "span_id": "s6"}],
        contract_id="C")
    assert len(out) == 2  # deduped, not double-added
    assert next(c for c in out if c.clause_id == "C:6:h").exception_of == "C:5:h"


def test_clause_evidence_is_span_text_without_the_function_label():
    # engine issue 0002 / ADR-0054: the KG function label is generation-only and is NOT placed in the evidence
    # text (it was the source of the auto-tag paraphrase leak; the SKILL judges by actual text, not the label).
    # Evidence is the clause's real span text -- no "[auto-tag: ...]" prefix and no asserted "Function: ..." one.
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import _clause_to_evidence

    c = CitedClause(contract_id="C", clause_id="C:7:h", function="Cap On Liability", span_id="s7")
    ev = _clause_to_evidence(c, "the Company shall not be liable for acts of God")
    assert ev is not None
    assert ev.text == "the Company shall not be liable for acts of God"  # just the real text
    assert "auto-tag" not in ev.text and "Cap On Liability" not in ev.text  # no function label at all


def test_clause_evidence_properties_are_out_of_band_not_in_the_text():
    # engine issue 0011 / ADR-0064: typed properties do NOT go into the evidence text (the [dim=value] schema
    # syntax the model paraphrased into prose). The span text is the whole text; the structured facts ride
    # out-of-band on EvidenceItem.properties (code-generated {dimension, value}), so narration is structurally
    # impossible -- the generator never sees "cap_quantum" or "=".
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import _clause_to_evidence

    c = CitedClause(contract_id="C", clause_id="C:7:h", function="Cap On Liability", span_id="s7",
                    properties=[CitedProperty(dimension="cap_quantum", value="12_months", edge_type="HAS",
                                              confidence="EXTRACTED", span_id="s7")])
    ev = _clause_to_evidence(c, "Supplier's total liability shall not exceed the fees paid in the prior year")
    assert ev is not None
    assert ev.text == "Supplier's total liability shall not exceed the fees paid in the prior year"  # body only
    assert "cap_quantum" not in ev.text and "=" not in ev.text and "[" not in ev.text  # no schema token leaks
    assert ev.properties == [{"dimension": "cap_quantum", "value": "12_months"}]  # structured, out-of-band


def test_clause_evidence_facts_only_is_humanized_and_out_of_band():
    # facts-only path (no span text): the facts are the only content, so they must stay citable -- but as
    # reader-safe natural text (no [dim=value] syntax), with the structured form still out-of-band (issue 0011).
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import _clause_to_evidence

    c = CitedClause(contract_id="C", clause_id="C:8:h", function="Cap On Liability", span_id="s8",
                    properties=[CitedProperty(dimension="cap_quantum", value="$500,000", edge_type="HAS",
                                              confidence="EXTRACTED", span_id="s8")])
    ev = _clause_to_evidence(c, None)
    assert ev is not None
    assert "[" not in ev.text and "=" not in ev.text and "cap_quantum" not in ev.text  # no schema syntax
    assert "$500,000" in ev.text  # still citable: the value survives in reader-safe form
    assert ev.properties == [{"dimension": "cap_quantum", "value": "$500,000"}]  # structured, out-of-band


def test_clause_evidence_contentless_returns_none():
    # no span text and no facts -> nothing citable -> dropped (returns None).
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import _clause_to_evidence

    c = CitedClause(contract_id="C", clause_id="C:9:h", function="Governing Law", span_id="s9")
    assert _clause_to_evidence(c, None) is None


def test_exception_clause_evidence_is_framed_and_tagged_inferred():
    from rag_wright.packs.contracts.subgraphs.intra_document_qa import _clause_to_evidence

    exc = CitedClause(contract_id="C", clause_id="C:6:h", function="Uncapped Liability",
                      span_id="s6", exception_of="C:5:h")
    ev = _clause_to_evidence(exc, "any negligence or fault")
    assert "Exception to the liability cap (inferred)" in ev.text and "negligence" in ev.text
    assert ev.confidence == "INFERRED"  # FR-S.4: a derived link is surfaced as inferred, human-validatable


# --- production wiring: defaults the answer model via answer_model_for (right path per profile) ---------------


def test_production_defaults_answer_model_via_answer_model_for(monkeypatch):
    """production_intra_document_qa with no injected answer_model builds it through answer_model_for, so a
    client_side_structured model (self-hosted Gemma) automatically gets the free-text tag-parse path."""
    from rag_wright.packs.contracts.subgraphs import intra_document_qa as idq

    seen = {}

    def _fake_answer_model_for(model_id=None, **kw):
        seen["model_id"] = model_id
        return object()  # a sentinel answer_model; generation isn't exercised here

    monkeypatch.setattr(idq, "_answer_model_for_impl", _fake_answer_model_for, raising=False)
    # build with no answer_model -> must call answer_model_for (via the module hook) with the given id
    idq.production_intra_document_qa(
        store=object(), answer_model_id="google/gemma-4-31B-it-qat-w4a16-ct")
    assert seen["model_id"] == "google/gemma-4-31B-it-qat-w4a16-ct"


async def test_production_serves_whole_contract_not_function_narrowed(monkeypatch):
    """ADR-0047: production `serve` uses `contract_clause_index` (the WHOLE contract) and never the
    `clauses_of_function` narrowing / the query function classifier -- a mislabel can't hide the real clause."""
    from rag_wright.capabilities import answer_generator as ag
    from rag_wright.packs.contracts.capabilities import contract_kg_serve as cks
    from rag_wright.capabilities.answer_generator import GeneratedAnswer
    from rag_wright.packs.contracts.subgraphs import intra_document_qa as idq

    calls = {"whole": 0, "by_function": 0}

    def _whole(store, contract_id, **kw):  # kw: 0006-D include_untyped
        calls["whole"] += 1
        return []  # no clauses -> empty evidence -> the stub generator abstains

    def _by_function(*a, **k):
        calls["by_function"] += 1
        return []

    monkeypatch.setattr(cks, "contract_clause_index", _whole)
    monkeypatch.setattr(cks, "clauses_of_function", _by_function, raising=False)

    async def _agen(q, ev, model=None):
        return GeneratedAnswer(answer="", citations=[], abstained=True)

    monkeypatch.setattr(ag, "agenerate_answer", _agen)

    class _Store:
        def kg_edges(self, *a, **k):  # EP-REF-1a-ii: exceptions_of_clause now reads via store.kg_edges
            return []

        def spans_by_document(self, cid, fns):
            return []

    leg = idq.production_intra_document_qa(store=_Store(), answer_model=object())
    await leg.ainvoke({"contract_id": "c1", "question": "how is liability capped?"})
    assert calls["whole"] == 1        # the whole-contract index WAS used
    assert calls["by_function"] == 0  # the function-narrowing path was NOT


async def test_production_bge_reranks_to_top_k_within_contract(monkeypatch):
    """ADR-0047 rework: with more than top_k clauses, `serve` BGE-reranks the contract's clauses to the question
    and serves only the top-K (bounded evidence) -- mislabel-robust (ranks by meaning) AND avoids dumping the
    whole 100-clause contract into generation."""
    from rag_wright.capabilities import answer_generator as ag
    from rag_wright.packs.contracts.capabilities import contract_kg_serve as cks
    from rag_wright.capabilities.answer_generator import GeneratedAnswer
    from rag_wright.packs.contracts.subgraphs import intra_document_qa as idq

    n, k = 20, 5
    clauses = [CitedClause(contract_id="c1", clause_id=f"c1:{i}:h", function="F",
                           span_id=f"c1:{i}:h#0", properties=[]) for i in range(n)]
    monkeypatch.setattr(cks, "contract_clause_index", lambda store, cid, **kw: list(clauses))
    monkeypatch.setattr(idq, "rehydrate_clause_texts",
                        lambda store, cid, cl: {c.clause_id: f"text-{c.clause_id}" for c in cl})

    class _RR:  # score ascending with index -> clause 19 highest; top-5 = indices 19,18,17,16,15
        def score(self, q, passages):
            return list(range(len(passages)))

    captured = {}

    async def _gen(q, ev, model=None):
        captured["ids"] = [e.chunk_id for e in ev]  # EvidenceItem.chunk_id == the clause_id
        return GeneratedAnswer(answer="", citations=[], abstained=True)

    monkeypatch.setattr(ag, "agenerate_answer", _gen)

    class _Store:
        def kg_edges(self, *a, **k):  # EP-REF-1a-ii: exceptions_of_clause now reads via store.kg_edges
            return []

        def spans_by_document(self, cid, fns):
            return []

    leg = idq.production_intra_document_qa(store=_Store(), reranker=_RR(), answer_model=object(), top_k=k)
    await leg.ainvoke({"contract_id": "c1", "question": "how is liability capped?"})
    assert len(captured["ids"]) == k  # generation saw only the top-K, not all 20
    assert set(captured["ids"]) == {f"c1:{i}:h" for i in range(15, 20)}  # the 5 highest-scored
