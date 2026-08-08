"""MCP-PROTO Phase A: validate the query-leg SUBGRAPHS end-to-end against the Modal KG + A100, no MCP.

Runs the REGISTERED leg subgraphs (not the direct-helper shortcuts adoption_query_validate.py used for A/C) so
any KG/model integration issue surfaces before MCP is layered on. Same substrate as Leg B's validation: the
Modal ArcadeDB KG (`ragwright_cuad_full`, https) + the A100 models (vLLM-Granite via the seam, BGE/LegalBERT via
the STACK_URL adapters).

  ARCADEDB_HOST=<rw-arcadedb>.modal.run ARCADEDB_PORT=443 ARCADEDB_PROTOCOL=https \
    ARCADEDB_USER=root ARCADEDB_PASSWORD=rag_wright_dev_2026 ARCADEDB_DATABASE=ragwright_cuad_full \
    RAG_SERVING=vllm VLLM_BASE_URL=<a100>/v1 VLLM_API_KEY=rw-vllm-dev-key STACK_URL=<a100> \
    uv run --no-sync python -m scripts.phase_a_leg_validate A

Arg selects the leg(s): A (intra_document_qa), Crel (relational_qa, graph-structural), B
(typed_property_retrieval, the corpus-wide function+property leg). No arg runs all three.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv


def _line(s: str = "") -> None:
    print(s, flush=True)


def validate_leg_a() -> None:
    """A1: intra_document_qa -- (contract_id, question) -> cited GeneratedAnswer, all on the Modal stack."""
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.intra_document_qa import production_intra_document_qa

    store = ArcadeDBStore.from_env(database="ragwright_cuad_full")
    llm_id = model_for(ModelRole.GENERAL)  # the configured GENERAL model (self-hosted Gemma, OpenRouter, ...)
    # answer model is defaulted via answer_model_for -> the RIGHT path per profile (free-text tag-parse for a
    # self-hosted Gemma, the structured-output seam otherwise). No hardcoded SeamAnswerModel.
    leg_a = production_intra_document_qa(store=store, function_model_id=llm_id, answer_model_id=llm_id)

    # pick a real contract that has a well-known clause function (the Modal KG is 506, so never the 4 recovered)
    row = store._query("SELECT clause_id FROM Clause WHERE function = 'Cap On Liability' LIMIT 1")
    if not row:
        _line("  [A1] no 'Cap On Liability' clause found in the KG -- cannot pick a test contract")
        store.close()
        return
    contract_id = row[0]["clause_id"].rsplit(":", 2)[0]
    question = "How is liability capped in this contract, and under what conditions?"
    _line("=" * 90)
    _line(f"LEG A -- intra_document_qa SUBGRAPH | contract={contract_id} | Q={question!r}")
    out = leg_a.invoke({"contract_id": contract_id, "question": question})
    ans = out["answer"]
    _line(f"  abstained: {ans.abstained}")
    _line(f"  citations ({len(ans.citations)}): {[c[-16:] for c in ans.citations[:6]]}")
    _line(f"  answer: {ans.answer.strip()[:400]}")
    _line(f"  [A1] {'OK -- cited answer' if (not ans.abstained and ans.citations) else 'CHECK -- abstained/uncited'}")
    store.close()


def validate_leg_c_rel() -> None:
    """A2: relational_qa -- GRAPH-STRUCTURAL evidence (cited by source contract, no text_store), on Modal."""
    from rag_wright.capabilities.answer_generator import SeamAnswerModel
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.relational_qa import production_relational_qa

    store = ArcadeDBStore.from_env(database="ragwright_cuad_full")
    row = store._query("SELECT entity_id, name FROM Entity WHERE both('Relationship').size() > 0 LIMIT 1")
    if not row:
        _line("  [A2] no Entity with a Relationship edge -- cannot pick a start entity")
        store.close()
        return
    start, name = row[0]["entity_id"], row[0]["name"]
    leg = production_relational_qa(store=store, answer_model=SeamAnswerModel(model_for(ModelRole.GENERAL)))
    _line("=" * 90)
    _line(f"LEG C-rel -- relational_qa SUBGRAPH (graph-structural) | start={name!r} ({start})")
    out = leg.invoke({"query": f"Which parties does {name} contract with?", "start_entity_id": start})
    ans = out["answer"]
    _line(f"  abstained: {ans.abstained} | citations (contracts): {ans.citations[:6]}")
    _line(f"  answer: {ans.answer.strip()[:400]}")
    _line(f"  [A2] {'OK -- cited answer' if (not ans.abstained and ans.citations) else 'CHECK -- abstained/uncited'}")
    store.close()


def validate_leg_b() -> None:
    """Leg B: typed_property_retrieval -- THE corpus-wide function+property retrieval leg (BGE+property pool via
    property_boosted_retrieval). Standardized on this after retiring the redundant cross_corpus_retrieval."""
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.remote_encoders import query_classifier, query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.typed_property_retrieval import production_typed_property_retrieval

    store = ArcadeDBStore.from_env(database="ragwright_cuad_full")
    leg = production_typed_property_retrieval(
        store=store, embedder=query_embedder(), classifier=query_classifier(),
        extract_model=default_extraction_model("query-constraints", "ibm-granite/granite-4.1-8b"),
        function_model_id=model_for(ModelRole.GENERAL), k=8)
    q = "cap on liability set at a multiple of the fees paid"
    _line("=" * 90)
    _line(f"LEG B -- typed_property_retrieval SUBGRAPH | Q={q!r}")
    results = leg.invoke({"query": q})["retrieval"].results
    _line(f"  -> top {len(results)} cited spans:")
    for r in results[:8]:
        tag = "  [MATCH]" if getattr(r, "matched", False) else ""
        _line(f"     {r.rank}. [{r.span_id[-16:]}] ({r.function}) {r.text[:70].strip()}...{tag}")
    _line(f"  [LegB] {'OK -- ranked cited spans' if results else 'CHECK -- empty results'}")
    store.close()


_LEGS = {"A": validate_leg_a, "Crel": validate_leg_c_rel, "B": validate_leg_b}


def main() -> None:
    load_dotenv()
    which = sys.argv[1] if len(sys.argv) > 1 else "A"
    for key in ([which] if which in _LEGS else list(_LEGS)):
        _LEGS[key]()


if __name__ == "__main__":
    main()
