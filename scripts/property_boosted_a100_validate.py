"""SPAN-CLAUSE-RERANK (b): the FULL A100-integrated property-boosted retrieval over CUAD-full.

The MS1-6 Leg B upgraded from pure span_hybrid_search to the property-boosted capability: granite extracts the
typed constraints + routes the functions (A100 vLLM), LegalBERT top-k routes too (A100 /classify), BGE embeds
(A100 /embed), then `property_boosted_retrieval` joins the BGE pool to clause props via `edge.span_id` and
reranks. Local KG `ragwright_cuad_full`; NO OpenRouter, NO local GPU.

  RAG_SERVING=vllm VLLM_BASE_URL=<stack>/v1 STACK_URL=<stack> \
  uv run python -m scripts.property_boosted_a100_validate
"""

from __future__ import annotations

from dotenv import load_dotenv


def _line(s=""):
    print(s, flush=True)


def main() -> None:
    load_dotenv()
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.property_boosted_retrieval import property_boosted_retrieval
    from rag_wright.capabilities.query_function_classifier import route_query
    from rag_wright.capabilities.remote_encoders import query_classifier, query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env()          # ragwright_cuad_full
    embedder = query_embedder()               # A100 /embed
    classifier = query_classifier()           # A100 /classify
    extract_model = default_extraction_model("query-constraints", "ibm-granite/granite-4.1-8b")  # A100 vLLM
    fn_model = model_for(ModelRole.GENERAL)   # granite via the seam

    queries = [
        "anti-assignment clauses that allow a party to freely assign without the other's consent",
        "cap on liability set at a multiple of the fees paid",
        "governing law of more than one jurisdiction",
    ]
    for q in queries:
        _line("=" * 92)
        _line(f"Q: {q!r}")
        constraints, llm_fns = route_query(q, extract_model=extract_model, function_model_id=fn_model, k=3)
        lb_fns = classifier.classify_topk([q], k=3)[0]
        functions = list(dict.fromkeys([*llm_fns, *lb_fns]))
        _line(f"   granite constraints: {constraints}")
        _line(f"   routed functions (granite ∪ LegalBERT): {functions}")
        results = property_boosted_retrieval(
            q, store=store, embedder=embedder, functions=functions, constraints=constraints, k=6)
        _line(f"   -> property-boosted top {len(results)} cited spans:")
        for r in results:
            tag = f"  [MATCH {r.matched}]" if r.matched else ""
            _line(f"      {r.rank}. [{r.span_id[-14:]}] ({r.function}) {r.text[:88].strip()}...{tag}")

    _line("=" * 92)
    _line("[SPAN-CLAUSE-RERANK b] property-boosted retrieval end-to-end on the self-hosted stack.")
    store.close()


if __name__ == "__main__":
    main()
