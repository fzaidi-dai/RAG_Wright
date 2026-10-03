"""SPAN-CLAUSE-RERANK: demonstrate the property-boost rerank over CUAD-full, enabled by the `edge.span_id`
clause<->span join (`store.span_properties`).

Pipeline (KG-5 V4 adopted shape): BGE base pool (`span_hybrid_search`, function-filtered) -> join each span to
its clause's typed props via `span_properties` -> `typed_constraint_match_rank` (stable: constraint-match
primary, BGE order as the tiebreak). Shows the boosted order surfaces constraint-matching spans that pure BGE
ranked lower. Uses a LOCAL BGE embedder (no A100 needed to prove the join+rerank); constraints are given
explicitly here to isolate the rerank (the granite constraint-extraction is the MS1-6 A100 front-door).

  uv run python -m scripts.typed_rerank_validate
"""

from __future__ import annotations

from dotenv import load_dotenv


def _line(s=""):
    print(s, flush=True)


def main() -> None:
    load_dotenv()
    from rag_wright.capabilities.remote_encoders import query_embedder  # local (STACK_URL unset)
    from rag_wright.capabilities.retrieval_core import typed_constraint_match_rank
    from rag_wright.contracts.value_match import constraint_match_count  # inject the domain matcher (EP-CORE-1b)
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env()  # ragwright_cuad_full
    embedder = query_embedder()       # local BGEM3Embedder

    demos = [
        ("restrictions on assigning or transferring this agreement", "Anti-Assignment",
         ("assignment_consent", "free"), "assignment_consent"),
        ("non-solicitation of the other party's customers", "No-Solicit Of Customers",
         ("nonsolicit_target", "customers"), "nonsolicit_target"),
    ]

    for query, function, constraint, dim in demos:
        _line("=" * 90)
        _line(f"Q: {query!r}  | function={function}  | constraint={constraint[0]}={constraint[1]!r}")
        dense, sparse = embedder.encode_dense(query), embedder.encode_sparse(query)
        pool = store.span_hybrid_search(dense, sparse, k=12, function=function)  # BGE order
        span_ids = [h["span_id"] for h in pool]
        props = store.span_properties(span_ids)

        def _val(sid):  # the span's value for the constraint dim (or '-')
            return next((v for d, v in props[sid] if d == dim), "-")

        # boosted: stable sort by constraint-match count; ties keep BGE (input) order
        boosted = typed_constraint_match_rank(
            {constraint}, [(sid, props[sid]) for sid in span_ids], match_count_fn=constraint_match_count).ranked
        boosted_ids = [r.clause_id for r in boosted]

        def _first_match_rank(order):  # 1-based rank of the first span matching the constraint
            for i, sid in enumerate(order, 1):
                if constraint in props[sid]:
                    return i
            return None

        _line(f"  pure BGE order   ({dim}):  " + " ".join(f"{_val(s):>16}" for s in span_ids[:6]))
        _line(f"  property-boosted ({dim}):  " + " ".join(f"{_val(s):>16}" for s in boosted_ids[:6]))
        n_match = sum(1 for s in span_ids if constraint in props[s])
        _line(f"  -> {n_match}/{len(span_ids)} pool spans match the constraint | "
              f"first match: BGE rank {_first_match_rank(span_ids)} -> boosted rank {_first_match_rank(boosted_ids)}")

    _line("=" * 90)
    _line("[SPAN-CLAUSE-RERANK] property boost re-orders the BGE pool via the edge.span_id clause<->span join.")
    store.close()


if __name__ == "__main__":
    main()
