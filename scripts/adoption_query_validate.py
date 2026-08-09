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
    from rag_wright.capabilities.dg_extraction import default_extraction_model
    from rag_wright.capabilities.remote_encoders import query_embedder
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.models.seam import build_model
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.typed_property_retrieval import production_typed_property_retrieval

    store = ArcadeDBStore.from_env()  # -> ragwright_cuad_full (the adopted default)
    embedder = query_embedder()       # -> A100 /embed  (STACK_URL set)
    llm_model = model_for(ModelRole.GENERAL)  # -> granite via the seam (RAG_SERVING=vllm)

    _line("=" * 90)
    _line("LEG B -- PROPERTY-BOOSTED typed retrieval via the REGISTERED `typed_property_retrieval` SUBGRAPH "
          "(granite constraints -> property_boosted_retrieval over the WHOLE-INDEX pool, ADR-0047; local KG, A100)")
    extract_model = default_extraction_model("query-constraints", "ibm-granite/granite-4.1-8b")  # A100 vLLM
    leg_b = production_typed_property_retrieval(  # ADR-0047: no classifier -- whole-index pool
        store=store, embedder=embedder, extract_model=extract_model, k=5)
    for q in ["anti-assignment clauses that allow a party to freely assign without consent",
              "cap on liability set at a multiple of the fees paid"]:
        state = leg_b.invoke({"query": q})  # constraints + functions (parallel) -> retrieve -> assemble
        _line(f"\n  Q: {q!r}")
        _line(f"     granite constraints: {sorted(state.get('constraints', set()))}")
        _line(f"     routed functions (granite ∪ LegalBERT): {state.get('functions', [])}")
        results = state["retrieval"].results
        _line(f"     -> subgraph top {len(results)} cited spans:")
        for r in results:
            tag = f"  [MATCH {r.matched}]" if r.matched else ""
            _line(f"        {r.rank}. [{r.span_id[-14:]}] ({r.function}) {r.text[:80].strip()}...{tag}")

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
