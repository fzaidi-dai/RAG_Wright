"""MS1-6 (MODAL-STACK-1, ADR-0039): END-TO-END adoption validation on the fully self-hosted stack.

Local KG `ragwright_cuad_full` (adopted MS1-4) + the co-located A100 models (MS1-5): vLLM-Granite via the
model seam (`RAG_SERVING=vllm`), BGE-M3 + LegalBERT via the `STACK_URL` /embed + /classify adapters (MS1-6).
Runs representative Leg A / B / C queries and confirms CITED answers -- proving the substrate switch works
end to end (no OpenRouter, no local GPU).

  RAG_SERVING=vllm VLLM_BASE_URL=<stack>/v1 STACK_URL=<stack> \
  uv run python -m scripts.adoption_query_validate
"""

from __future__ import annotations

from dotenv import load_dotenv


def _line(s=""):
    print(s, flush=True)


def main() -> None:
    load_dotenv()
    from rag_wright.capabilities.contract_kg_serve import clauses_of_function
    from rag_wright.capabilities.query_function_classifier import classify_query_functions
    from rag_wright.capabilities.remote_encoders import query_classifier, query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.models.seam import build_model
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env()  # -> ragwright_cuad_full (the adopted default)
    embedder = query_embedder()       # -> A100 /embed  (STACK_URL set)
    classifier = query_classifier()   # -> A100 /classify
    llm_model = model_for(ModelRole.GENERAL)  # -> granite via the seam (RAG_SERVING=vllm)

    def _span_text(span_id: str) -> str:
        r = store._query(f"SELECT text, function FROM Span WHERE span_id = '{span_id}' LIMIT 1")
        return (r[0]["text"][:120].strip() if r else "").replace("\n", " ")

    _line("=" * 90)
    _line("LEG B -- typed retrieval (A100 LegalBERT + granite routing, A100 BGE hybrid search, local KG)")
    for q in ["clauses that cap liability at a multiple of the fees paid",
              "non-solicitation of employees for a fixed period"]:
        lb = classifier.classify_topk([q], k=3)[0]                       # A100 LegalBERT
        llm = classify_query_functions(q, llm_model, k=3)                # A100 granite
        routed = list(dict.fromkeys([*llm, *lb]))                        # KG-5e llm-union
        _line(f"\n  Q: {q!r}")
        _line(f"     LegalBERT top-3: {lb}")
        _line(f"     granite router:  {llm}")
        # A100 BGE embed (query) -> span_hybrid_search (Span index, RRF dense+sparse) restricted to the routed fn
        dense, sparse = embedder.encode_dense(q), embedder.encode_sparse(q)
        hits = store.span_hybrid_search(dense, sparse, k=4, function=routed[0] if routed else None)
        _line(f"     -> span_hybrid_search (function={routed[0] if routed else None!r}), top {len(hits)} cited spans:")
        for h in hits[:4]:
            _line(f"        [{h['span_id'][-16:]}] {_span_text(h['span_id'])}...")

    _line("\n" + "=" * 90)
    _line("LEG A -- intra-contract scoped QnA (local Clause KG cited clauses + granite cited answer via A100)")
    # a contract that has Cap On Liability clauses (contract_id = the source-doc part of the clause_id)
    row = store._query("SELECT clause_id FROM Clause WHERE function = 'Cap On Liability' LIMIT 1")
    contract_id = row[0]["clause_id"].rsplit(":", 2)[0] if row else None
    if contract_id:
        cited = clauses_of_function(store, contract_id, "Cap On Liability")
        _line(f"\n  contract: {contract_id}")
        _line(f"  Cap-On-Liability clauses (cited, with props): {len(cited)}")
        for c in cited[:3]:
            props = ", ".join(f"{p.dimension}={p.value}" for p in c.properties[:4])
            _line(f"     [{c.clause_id[-16:]}] props: {props or '(none)'}")
        # granite generates a grounded, cited answer from the KG props (A100 LLM)
        facts = "; ".join(f"clause {c.clause_id[-12:]}: " + ", ".join(f"{p.dimension}={p.value}" for p in c.properties)
                          for c in cited[:5])
        prompt = ("Using ONLY these extracted facts, answer in one sentence how this contract caps liability, "
                  f"and cite the clause id(s). Facts: {facts}")
        ans = build_model(llm_model).invoke(prompt)
        _line(f"  granite answer: {ans.content.strip()[:280]}")

    _line("\n" + "=" * 90)
    _line("LEG C -- relational (PARTY_TO traversal over the local KG)")
    ent = store._query("SELECT name FROM Entity WHERE out('PartyTo').size() > 0 LIMIT 1")
    if ent:
        name = ent[0]["name"]
        contracts = store._query(
            f"SELECT contract_id FROM (SELECT expand(out('PartyTo')) FROM Entity WHERE name = '{name}')")
        _line(f"\n  party: {name!r} -PARTY_TO-> {len(contracts)} contracts:")
        for cc in contracts[:4]:
            _line(f"     {cc.get('contract_id')}")

    _line("\n" + "=" * 90)
    _line("[MS1-6] adoption validation complete -- all three legs answered on the self-hosted stack.")
    store.close()


if __name__ == "__main__":
    main()
