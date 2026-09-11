"""LG-3d: the `contract_ingestion_pipeline` composite -- hermetic (stub stages + fake adapter, no LLM/DB).

The GENERIC ingestion pipeline (corpus-agnostic): chunk -> [extract_clauses || extract_graph] -> resolve ->
write, per document, with a per-document dead-letter so one bad document never kills the corpus ingest. The
corpus driver maps a `CorpusAdapter`'s documents through the pipeline.
Adding a corpus = writing one adapter, never re-implementing the flow.
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.subgraphs.contract_ingestion_pipeline import (
    IngestionReport,
    SourceDocument,
    clause_extraction_jobs,
    per_contract_graph_extraction,
    seed_chunk_cache,
    seed_party_cache,
)

_FAST_RETRY = RetryPolicy(max_attempts=2, initial_interval=0.0)


def _span(text: str, span_index: int = 0, chunk: str = "chunk"):
    from rag_wright.spans.segment import OperativeSpan

    return OperativeSpan(
        span_id=f"{chunk}#{span_index}", parent_chunk_id=chunk, parent_okf_path="p",
        span_index=span_index, start=0, end=len(text), text=text)


# --- issue 0038: a Clause is a PROVISION (numbered section, else chunk), NOT a sentence. clause_extraction_jobs
# groups contiguous spans into provisions and emits one job per provision: (index, anchor_op, function, scores,
# merged_text). Retrieval stays per span. ---------------------------------------------------------------------
def test_numbered_sections_group_into_one_job_each():
    segments = [
        (_span("2.1. Supply. During the term HOVIONE shall supply the API.", 0), "Supply", 0, []),
        (_span("The API shall meet the Product Specifications.", 1), "Supply", 0, []),   # continuation -> same provision
        (_span("2.2. Payment. INTERSECT shall pay within 30 days.", 2), "Payment", 0, []),
        (_span("(i) net of taxes; (ii) in USD.", 3), "Payment", 0, []),                  # list item -> same provision
    ]
    jobs = clause_extraction_jobs(segments)
    assert len(jobs) == 2                                             # two provisions, not four sentences
    idx0, anchor0, fn0, _s0, text0 = jobs[0]
    assert idx0 == 0 and anchor0.span_id == "chunk#0"                 # anchor = the section heading span
    assert "2.1. Supply." in text0 and "Product Specifications" in text0  # continuation merged in
    assert "(i) net of taxes" in jobs[1][4]                          # list item merged into provision 2


def test_deep_numbered_list_items_fold_into_their_provision():
    # issue 0039: a section number is DEPTH-CAPPED at two levels. '10.5.' starts a provision; the deeper
    # '10.5.1.' / '10.5.1.1.' are list items WITHIN it and fold in (0038's own rule), not their own clauses.
    from rag_wright.spans.segment import starts_new_provision
    assert starts_new_provision("10.5. Obligations on Termination. Upon expiry the Supplier shall:") is True
    assert starts_new_provision("10.5.1. return all Confidential Information within thirty days") is False
    assert starts_new_provision("10.5.1.1. including all copies and derivatives thereof") is False
    segments = [
        (_span("10.5. Obligations on Termination. Upon expiry the Supplier shall:", 0), "General", 0, []),
        (_span("10.5.1. return all Confidential Information within thirty days", 1), "General", 0, []),
        (_span("10.5.1.1. including all copies and derivatives thereof", 2), "General", 0, []),
        (_span("10.6. Survival. The confidentiality obligations survive termination.", 3), "General", 0, []),
    ]
    jobs = clause_extraction_jobs(segments)
    assert len(jobs) == 2                                          # 10.5 (with its nested items folded) + 10.6
    assert jobs[0][4].count("\n") == 2                             # 10.5 merged its two deeper list items
    assert jobs[1][4].startswith("10.6.")


def test_untagged_provision_still_becomes_a_job():
    # issue 0036 preserved: an untagged (function=NONE) provision is still extracted (function-independent).
    from rag_wright.contracts.function import NO_FUNCTION

    segments = [(_span("3.1. Term. Neither party shall be liable for indirect damages.", 0), NO_FUNCTION, 0, [])]
    jobs = clause_extraction_jobs(segments)
    assert len(jobs) == 1 and jobs[0][2] == NO_FUNCTION


def test_a_chunk_change_is_a_provision_boundary():
    # two heading-less spans in DIFFERENT chunks -> two provisions (never merged across the chunker's breaks).
    segments = [
        (_span("The parties agree to cooperate in good faith.", 0, chunk="cA"), "General", 0, []),
        (_span("Each party shall bear its own costs.", 0, chunk="cB"), "General", 0, []),
    ]
    assert len(clause_extraction_jobs(segments)) == 2


def test_heading_less_spans_in_one_chunk_fall_back_to_one_provision():
    # no numbered/heading markers, same chunk -> ONE provision (chunk-level floor), never one clause per sentence.
    segments = [
        (_span("The parties agree to cooperate in good faith.", 0), "General", 0, []),
        (_span("Each party shall bear its own costs.", 1), "General", 0, []),
        (_span("This paragraph has no section number at all.", 2), "General", 0, []),
    ]
    jobs = clause_extraction_jobs(segments)
    assert len(jobs) == 1
    assert jobs[0][4].count("\n") == 2                               # all three sentences merged into one provision


def test_unnumbered_standalone_heading_splits_but_folded_heading_degrades_to_chunk():
    # graceful degradation, middle tier: an un-numbered STANDALONE heading (no terminal punctuation) starts a
    # provision; a heading-less run (or a heading folded into its body) stays one chunk-level provision. Either
    # way the floor holds -- never one clause per sentence.
    from rag_wright.spans.segment import starts_new_provision
    assert starts_new_provision("Governing Law") is True          # standalone Title-case heading -> boundary
    assert starts_new_provision("CONFIDENTIALITY") is True         # standalone ALL-CAPS heading -> boundary
    assert starts_new_provision("Each party shall keep the other's information confidential.") is False
    segments = [
        (_span("Governing Law", 0), "General", 0, []),             # heading span -> its own provision start
        (_span("This Agreement is governed by the laws of Delaware.", 1), "General", 0, []),
        (_span("The parties submit to the courts of Delaware.", 2), "General", 0, []),
    ]
    jobs = clause_extraction_jobs(segments)
    assert len(jobs) == 1                                          # heading + its two sentences = ONE provision
    assert jobs[0][4].startswith("Governing Law")


def test_furniture_is_dropped_and_an_all_furniture_provision_yields_no_clause():
    segments = [
        (_span("5.1. Signatures.", 0), "General", 0, []),
        (_span("By: /s/ Jane Doe", 1), "General", 0, []),            # signature furniture (dropped from merged text)
        (_span("9", 0, chunk="cZ"), "General", 0, []),               # a lone page number chunk -> no clause
    ]
    jobs = clause_extraction_jobs(segments)
    assert len(jobs) == 1                                            # only the 5.1 provision (its furniture dropped)
    assert jobs[0][4] == "5.1. Signatures."                          # the "By:" line is not in the merged text




















def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.contract_ingestion_pipeline import register_contract_ingestion_pipeline

    reg = CapabilityRegistry()
    register_contract_ingestion_pipeline(reg)
    assert reg.get("contract_ingestion_pipeline").kind == "subgraph"
    assert reg.get("contract_ingestion_pipeline").contract is IngestionReport


# --- INGEST-REFACTOR (a): per-contract GP-1B + cache reuse ---------------------------------------------------

def test_seed_party_cache_canonicalizes_keys_and_is_idempotent(tmp_path):
    import json

    legacy = tmp_path / "dg_extracted_parties.json"
    # the legacy key is the RAW title (spaces/commas); the pipeline addresses by canonical source_doc_id (HYG-1)
    legacy.write_text(json.dumps({"ACME CO_01_2020-EX-10-SUPPLY AGREEMENT": ["Acme Co.", "Beta LLC"]}))
    party_dir = tmp_path / "graph_parties"

    assert seed_party_cache(party_dir, legacy) == 1
    seeded = party_dir / "ACME_CO_01_2020-EX-10-SUPPLY_AGREEMENT.json"  # canonicalized: spaces -> underscores
    assert json.loads(seeded.read_text()) == ["Acme Co.", "Beta LLC"]
    assert seed_party_cache(party_dir, legacy) == 0  # idempotent: never re-writes an existing entry


def test_seed_party_cache_missing_legacy_is_noop(tmp_path):
    assert seed_party_cache(tmp_path / "graph_parties", tmp_path / "does_not_exist.json") == 0


def test_seed_chunk_cache_copies_manifests_only_once(tmp_path):
    legacy = tmp_path / "legacy_chunks"
    legacy.mkdir()
    (legacy / "DocA.deadbeefcafe0001.chunks.json").write_text("{}")
    (legacy / "skip.txt").write_text("not a manifest")
    chunk_dir = tmp_path / "chunks"

    assert seed_chunk_cache(chunk_dir, legacy) == 1  # only the .chunks.json manifest
    assert (chunk_dir / "DocA.deadbeefcafe0001.chunks.json").exists()
    assert seed_chunk_cache(chunk_dir, legacy) == 0  # idempotent


def test_per_contract_graph_reuses_seeded_names_without_extracting(tmp_path):
    import json

    party_dir = tmp_path / "graph_parties"
    party_dir.mkdir()
    (party_dir / "C1.json").write_text(json.dumps(["Acme Co.", "Beta LLC"]))

    def _must_not_extract(_text):
        raise AssertionError("names_fn called despite a seeded cache hit")

    out = per_contract_graph_extraction(
        SourceDocument(source_doc_id="C1", text="t"), party_dir=party_dir, names_fn=_must_not_extract)

    assert len(out) == 1  # ONE ExtractionResult per contract (not per chunk)
    result = out[0]
    assert [m.text for m in result.entity_mentions] == ["Acme Co.", "Beta LLC"]
    assert len(result.relationship_facts) == 1  # a CONTRACTS_WITH edge between the two parties
    assert result.chunk_id.source_doc_id == "C1"  # provenance stays on the contract (KG-7 join)


def test_per_contract_graph_extracts_once_then_caches(tmp_path):
    party_dir = tmp_path / "graph_parties"
    calls = {"n": 0}

    def _extract(_text):
        calls["n"] += 1
        return ["Acme Co."]

    doc = SourceDocument(source_doc_id="C2", text="body")
    first = per_contract_graph_extraction(doc, party_dir=party_dir, names_fn=_extract)
    second = per_contract_graph_extraction(doc, party_dir=party_dir, names_fn=_extract)

    assert calls["n"] == 1  # extracted ONCE; the second call served the freshly-written cache
    assert len(first) == len(second) == 1
    assert [m.text for m in first[0].entity_mentions] == ["Acme Co."]
    assert first[0].relationship_facts == []  # a lone party yields a mention but no edge


def test_per_contract_graph_no_parties_yields_no_extraction(tmp_path):
    out = per_contract_graph_extraction(
        SourceDocument(source_doc_id="C3", text="t"), party_dir=tmp_path, names_fn=lambda _t: [])
    assert out == []


# (issue 0028 / ADR-0091: the KG-7 `corpus_party_link_fn` / PartyTo link step was retired; its tests were removed
#  with it. `arun_corpus_ingestion(link_fn=...)` keeps the generic no-op seam, covered by test_async_ingestion.)


# --- PROD-3 lossless invariant (ADR-0050): no silent partial success -------------------------------------------








# --- issue 0033 follow-up: the ingest extraction models are caller-configurable --------------------------------

def test_ingest_extraction_models_are_caller_configurable(monkeypatch, tmp_path):
    """extract_model / list_model / samples / graph_extract_model / judge_model on aproduction_document_ingest
    thread to the right constructors (a bare model-id string is wrapped into an ExtractionModel for the clause
    extractor), so a caller no longer needs env vars to change any ingest model."""
    import pytest

    from rag_wright.subgraphs import contract_ingestion_pipeline as pipe

    captured: dict = {}

    class _StopHere(Exception):
        pass

    def _fake_granite(model=None, *, semantic_judge_fn=None, asemantic_judge_fn=None,
                      list_model=None, samples=None):
        captured.update(model=model, list_model=list_model, samples=samples)
        return object()  # dummy extractor; let wiring continue to the party-extraction call

    def _fake_party(**kw):
        captured.update(graph_kw=kw)
        raise _StopHere  # party extraction is the last model wiring -> stop before the network-y rest

    monkeypatch.setattr("rag_wright.spans.clause_kg_extractor.granite_clause_extractor", _fake_granite)
    monkeypatch.setattr("rag_wright.spans.semantic_judge.build_asemantic_judge_fn",
                        lambda mid: captured.update(judge_id=mid) or (lambda *a, **k: None))
    monkeypatch.setattr("rag_wright.capabilities.graph_extraction.aproduction_extract_fn", _fake_party)
    monkeypatch.setattr("rag_wright.capabilities.rlm_chunking.StructuralModelFallbackDiscoverer",
                        lambda model_id=None, **kw: captured.update(chunk_model_id=model_id))

    with pytest.raises(_StopHere):
        pipe.aproduction_document_ingest(
            store=object(), cache_dir=str(tmp_path), registry=object(), embedder=object(),
            extract_model="some/model-x", list_model="gemma-y", samples=3,
            graph_extract_model="party/model-z", judge_model="judge/model-q", chunk_model="chunk/model-c")
    assert captured["list_model"] == "gemma-y" and captured["samples"] == 3
    assert getattr(captured["model"], "model", None) == "some/model-x"  # bare id -> ExtractionModel
    assert captured["judge_id"] == "judge/model-q"                      # ingest semantic-judge model
    assert captured["graph_kw"] == {"model_id": "party/model-z"}        # party + affiliation share this
    assert captured["chunk_model_id"] == "chunk/model-c"                # chunker boundary-refinement model

    captured.clear()
    with pytest.raises(_StopHere):
        pipe.aproduction_document_ingest(
            store=object(), cache_dir=str(tmp_path), registry=object(), embedder=object())
    # no args -> backend/env defaults preserved (existing callers unaffected)
    assert captured["model"] is None and captured["list_model"] is None and captured["samples"] is None
    assert captured["graph_kw"] == {}  # party extraction falls back to its own default
    assert captured["judge_id"]  # judge falls back to model_for(STRUCTURED_REASONING), a non-empty id
    assert captured["chunk_model_id"] is None  # chunker falls back to model_for(GENERAL)
