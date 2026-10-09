"""KG-5 (FR-Q, ADR-0033): the KG-PRIMARY retrieval eval -- the KG (symbolic) leads, LLM assists minimally.

Front door (both sides = same extractor, same schema): the query is run through `extract_clause`
(granite-4.1-8b + `clause_template`, KG-2) -> typed (dimension, value) constraints, exactly as the clauses
were. Matching is symbolic: `value_match` (jurisdiction-canonicalized + subsumption-aware, KG-5a).

V2 = GRADED RANK: rank the candidates by how many query constraints their (grounded) typed props satisfy --
recall-safe (keeps all), and NO per-clause LLM call (the pointwise-Gemma bottleneck is gone). Ties are broken
deterministically here; V3 (one listwise Gemma call) / V4 (embedding) will break them meaningfully.

Candidate set MODE: `pool` (oracle-function pool, isolates the KG ranking -- default) or `corpus` (all clauses,
the KG-primary / function-as-soft-constraint mode -- the KG-5c A/B). Query extractions are cached
(`data/models/kg_query_constraints.jsonl`) so only the first run calls granite (~57 calls); re-ranking is free.

Reports condensed recall@10/@20 + nDCG@10 (the reliable pooled metrics) + per-query LLM-call count.

  uv run python -m eval.kg_primary
  MODE=corpus uv run python -m eval.kg_primary
"""

from __future__ import annotations

import json
import os
import re
import statistics
from pathlib import Path

from dotenv import load_dotenv

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.packs.contracts.capabilities.contract_kg_store import ContractKGStore  # EP-REF-1a-ii: typed reads via the domain store
from rag_wright.packs.contracts.capabilities.dg_extraction import extract_clause, openrouter_model
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.packs.contracts.schemas.value_match import constraint_match_count
from rag_wright.packs.contracts.spans.clause_kg_extractor import clause_to_record
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _sql_str, _str_array
from rag_wright.packs.contracts.capabilities.contract_kg_store import CLAUSE_TYPE
from rag_wright.util.concurrent import map_concurrent

DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")
# Candidate-set routing (replaces the ORACLE): pool (oracle function -- reference) |
# classifier (KG-5c A: LegalBERT union-top-`FUNCTION_TOPK` pool; TOPK=3 is KG-5e lever c) |
# corpus (KG-5c B: KG-primary, whole corpus, no pool) |
# hybrid (KG-5c C: whole corpus + LegalBERT top-2 as a SOFT type-boost) |
# route (KG-5e lever d: pool from the granite query DIMENSIONS via the CUAD-derived dim->function prior) |
# union (KG-5e: classifier top-K UNION dimension-route -- both routing signals pooled together) |
# llm (KG-5e lever b: taxonomy-constrained LLM query->function classifier pool -- second granite call) |
# llm_union (KG-5e: LLM functions UNION LegalBERT top-K -- the combination of b with c)
MODE = os.environ.get("MODE", "pool")
FUNCTION_MODEL = Path(os.environ.get("FUNCTION_MODEL", "data/models/legalbert_function"))
FUNCTION_TOPK = int(os.environ.get("FUNCTION_TOPK", "2"))  # KG-5e lever c: union-top-K classifier pool
ROUTE_MAP = Path(os.environ.get("ROUTE_MAP", "data/models/dimension_function_map.json"))
ROUTE_K = int(os.environ.get("ROUTE_K", "2"))  # KG-5e lever d: top-K functions from the dimension prior
ROUTE_SCORE = os.environ.get("ROUTE_SCORE", "conditional")  # conditional | lift | pmi (KG-5e lift fix)
ROUTE_MIN_SUPPORT = int(os.environ.get("ROUTE_MIN_SUPPORT", "1"))
# KG-5d noise: `CLEAN=oracle` ranks with only the query constraints a GOLD-relevant clause supports (an
# upper-bound ablation -- uses the gold to clean the query, so it BOUNDS the headroom of a real noise filter,
# not a deployable method). The supported/spurious prevalence is printed whenever cleaning is computed.
CLEAN = os.environ.get("CLEAN", "none")  # none | oracle
# KG-5d close-the-gap lever: `MATCH=idf` weights each satisfied query constraint by log(N/df) -- a constraint
# whose value matches MANY corpus clauses (non-discriminative, e.g. a near-universal enum) counts less than a
# rare one; `count` is the plain integer #-satisfied (default).
MATCH = os.environ.get("MATCH", "count")  # count | idf
# KG-6: `RANK=dense` ignores the KG constraint match (rank by BGE embedding only) -- the dense-only baseline
# the typed-KG ranking is A/B'd against, per query-hardness bucket (#constraints). `kg` = the adopted match.
RANK = os.environ.get("RANK", "kg")  # kg | dense
VARIANT = os.environ.get("VARIANT", "v2")  # v2 (arbitrary tiebreak) | v4 (BGE embedding-cosine tiebreak)
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")
QMODEL = os.environ.get("QUERY_MODEL", "ibm-granite/granite-4.1-8b")
# Model-keyed so a query-side model comparison (granite vs deepseek-pro vs kimi) re-extracts per model
# rather than silently reusing another model's cached constraints.
QCACHE = Path(f"data/models/kg_query_constraints_{_SLUG.sub('-', QMODEL)}.jsonl")

# KG-5e lever b: taxonomy-constrained LLM query->function classifier (a SECOND, separate granite call)
LLM_FUNCTION_MODEL = os.environ.get("LLM_FUNCTION_MODEL", "ibm-granite/granite-4.1-8b")
LLM_K = int(os.environ.get("LLM_K", "3"))
LFCACHE = Path(f"data/models/kg_query_functions_{_SLUG.sub('-', LLM_FUNCTION_MODEL)}_k{LLM_K}.jsonl")


def _device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _clause_key(acord_id: str, text: str) -> str:
    return str(ChunkId.of("acord-" + _SLUG.sub("-", acord_id), 0, text))


def query_constraints(queries) -> dict[str, set]:
    """Extract each query's typed constraints via granite + clause_template (same extractor as the KG).
    Cached to jsonl -- only new queries call granite."""
    cache: dict[str, list] = {}
    if QCACHE.exists():
        for line in QCACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                cache[r["text"]] = r["constraints"]
    todo = [q for q in queries if q.text not in cache]
    if todo:
        model = openrouter_model("granite-query", QMODEL)

        def _extract(q):
            try:  # a single query's extraction failure (e.g. docling-graph "no models") must not crash the eval
                cl = extract_clause(q.text, model)
            except Exception:  # noqa: BLE001 - treat an extraction failure as no constraints for this query
                return []
            if cl is None:
                return []
            rec = clause_to_record(cl, chunk_id=ChunkId.of("q", 0, q.text), function="Cap On Liability")
            return [[a.dimension.value, a.value] for a in rec.assertions]

        print(f"[query-extract] {len(todo)} queries via {QMODEL} (granite+clause_template)", flush=True)
        got = map_concurrent(todo, _extract, max_concurrency=6, label="[query-extract]", echo=True)
        QCACHE.parent.mkdir(parents=True, exist_ok=True)
        with QCACHE.open("a", encoding="utf-8") as f:
            for q, cons in zip(todo, got):
                f.write(json.dumps({"text": q.text, "constraints": cons}) + "\n")
                cache[q.text] = cons
    return {q.query_id: {tuple(c) for c in cache[q.text]} for q in queries}


def query_functions(queries) -> dict[str, list]:
    """KG-5e lever b: each query's ranked taxonomy functions via the SEPARATE granite call (one task per
    call). Cached to jsonl keyed by model+K -- only new queries call granite."""
    cache: dict[str, list] = {}
    if LFCACHE.exists():
        for line in LFCACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                cache[r["text"]] = r["functions"]
    todo = [q for q in queries if q.text not in cache]
    if todo:
        from rag_wright.packs.contracts.capabilities.query_function_classifier import classify_query_functions

        def _cls(q):
            return classify_query_functions(q.text, LLM_FUNCTION_MODEL, k=LLM_K)

        print(f"[query-function] {len(todo)} queries via {LLM_FUNCTION_MODEL} (k={LLM_K})", flush=True)
        got = map_concurrent(todo, _cls, max_concurrency=6, label="[query-function]", echo=True)
        LFCACHE.parent.mkdir(parents=True, exist_ok=True)
        with LFCACHE.open("a", encoding="utf-8") as f:
            for q, fns in zip(todo, got):
                f.write(json.dumps({"text": q.text, "functions": fns}) + "\n")
                cache[q.text] = fns
    return {q.query_id: cache[q.text] for q in queries}


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    queries = load_test_queries()
    constraints = query_constraints(queries)

    # V4/V3: BGE dense embeddings for the within-tie-group tiebreak (encode corpus + queries once, no LLM).
    # Cached to disk (corpus is fixed) so the KG-5c A/B/C sweep shares one encode instead of re-embedding thrice.
    clause_emb: dict[str, list] = {}
    query_emb: dict[str, list] = {}
    if VARIANT in ("v4", "v3"):
        ecache = Path(f"data/models/kg_v4_emb_{_SLUG.sub('-', DB)}.json")
        if ecache.exists():
            blob = json.loads(ecache.read_text(encoding="utf-8"))
            clause_emb, query_emb = blob["clause"], blob["query"]
            print(f"[v4] loaded cached embeddings {len(clause_emb)} clauses + {len(query_emb)} queries", flush=True)
        else:
            from rag_wright.capabilities.embedding import BGEM3Embedder

            emb = BGEM3Embedder()
            cids = list(corpus)
            dvecs, _ = emb.encode_batch([corpus[c] for c in cids])
            clause_emb = {c: list(v) for c, v in zip(cids, dvecs)}
            qvecs, _ = emb.encode_batch([q.text for q in queries])
            query_emb = {q.query_id: list(v) for q, v in zip(queries, qvecs)}
            ecache.parent.mkdir(parents=True, exist_ok=True)
            ecache.write_text(json.dumps({"clause": clause_emb, "query": query_emb}), encoding="utf-8")
            print(f"[v4] embedded + cached {len(clause_emb)} clauses + {len(query_emb)} queries", flush=True)

    # KG-5c A/C: real LegalBERT union-top-K on the query (replaces the oracle) + clause->function map
    qfuncs: dict[str, list] = {}   # query_id -> top-K predicted functions
    clause_fn: dict[str, set] = {}  # clause_id -> its span function tag(s), for the hybrid type-boost
    if MODE in ("classifier", "hybrid", "union", "llm_union"):
        from rag_wright.packs.contracts.spans.legalbert_classifier import LegalBertFunctionClassifier

        clf = LegalBertFunctionClassifier.load(FUNCTION_MODEL, device=_device())
        tops = clf.classify_topk([q.text for q in queries], k=(2 if MODE == "hybrid" else FUNCTION_TOPK))
        qfuncs = {q.query_id: t for q, t in zip(queries, tops)}
        for r in store._query(f"SELECT parent_okf_path, function FROM {SPAN_TYPE}"):
            clause_fn.setdefault(r["parent_okf_path"], set()).add(r["function"])
        print(f"[kg-5c] LegalBERT top-{2 if MODE=='hybrid' else FUNCTION_TOPK} for {len(qfuncs)} queries",
              flush=True)

    # KG-5e lever d: route each query to functions from its granite DIMENSIONS via the held-out prior
    route_of: dict[str, list] = {}
    if MODE in ("route", "union"):
        from rag_wright.packs.contracts.schemas.function_routing import route_functions

        cooc = json.loads(ROUTE_MAP.read_text(encoding="utf-8"))
        for q in queries:
            dims = {dim for (dim, _val) in constraints[q.query_id]}
            route_of[q.query_id] = route_functions(
                dims, cooc, k=ROUTE_K, score=ROUTE_SCORE, min_support=ROUTE_MIN_SUPPORT)
        avg = statistics.mean(len(v) for v in route_of.values()) if route_of else 0.0
        print(f"[kg-5e] dimension-routed {len(route_of)} queries via {ROUTE_MAP} "
              f"(k={ROUTE_K}, score={ROUTE_SCORE}, min_support={ROUTE_MIN_SUPPORT}, "
              f"avg {avg:.1f} functions/query)", flush=True)

    # KG-5e lever b: the taxonomy-constrained LLM function classification (separate granite call), cached
    llmfuncs: dict[str, list] = {}
    if MODE in ("llm", "llm_union"):
        llmfuncs = query_functions(queries)
        avg = statistics.mean(len(v) for v in llmfuncs.values()) if llmfuncs else 0.0
        print(f"[kg-5e b] LLM-routed {len(llmfuncs)} queries via {LLM_FUNCTION_MODEL} "
              f"(avg {avg:.1f} functions/query)", flush=True)

    corpus_ids = list(corpus)

    def oracle_f1(gold):
        reach: dict[str, int] = {}
        for g in gold:
            for f in {r["function"] for r in store._query(
                    f"SELECT function FROM {SPAN_TYPE} WHERE parent_okf_path = {_sql_str(g)}")}:
                reach[f] = reach.get(f, 0) + 1
        return max(reach, key=reach.get) if reach else None

    def _function_pool(fns):
        fns = [f for f in fns if f and f != "NONE"]
        return [] if not fns else list(dict.fromkeys(r["parent_okf_path"] for r in store._query(
            f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE primary_tag IN {_str_array(fns)}")))

    def pool_of(q):
        if MODE in ("corpus", "hybrid"):  # KG-primary: the whole clause corpus (no hard function filter)
            return corpus_ids
        if MODE == "classifier":  # KG-5c A / KG-5e c: LegalBERT union-top-K function pool
            return _function_pool(qfuncs[q.query_id])
        if MODE == "route":  # KG-5e d: dimension-prior-routed function pool
            return _function_pool(route_of[q.query_id])
        if MODE == "union":  # KG-5e: classifier top-K UNION dimension-route
            return _function_pool(list(dict.fromkeys(qfuncs[q.query_id] + route_of[q.query_id])))
        if MODE == "llm":  # KG-5e b: taxonomy-constrained LLM function pool
            return _function_pool(llmfuncs[q.query_id])
        if MODE == "llm_union":  # KG-5e: LLM functions UNION LegalBERT top-K (b combined with c)
            return _function_pool(list(dict.fromkeys(llmfuncs[q.query_id] + qfuncs[q.query_id])))
        return _function_pool([oracle_f1(sorted(q.relevant))])  # pool (reference): oracle function-filtered

    # per-clause grounded typed props (drop AMBIGUOUS), cached across queries
    props_cache: dict[str, set] = {}

    def props_of(acord_id):
        if acord_id not in props_cache:
            props_cache[acord_id] = {
                (e["dimension"], e["value"])
                for e in ContractKGStore(store).clause_typed_edges(_clause_key(acord_id, corpus.get(acord_id, "")))
                if e.get("confidence") != "AMBIGUOUS"
            }
        return props_cache[acord_id]

    # KG-5d noise measurement: a query constraint is "supported" when some GOLD-relevant clause's grounded
    # props satisfy it (via value_match); the rest are candidate noise. Prevalence is reported; CLEAN=oracle
    # additionally ranks with only the supported ones (the upper-bound ablation).
    eff_constraints = dict(constraints)
    if CLEAN == "oracle":
        n_sup = n_spu = 0
        cleaned: dict[str, set] = {}
        for q in queries:
            qc = constraints[q.query_id]
            gold_union: set = set()
            for g in q.relevant:
                gold_union |= props_of(g)
            supported = {c for c in qc if constraint_match_count({c}, gold_union) >= 1}
            cleaned[q.query_id] = supported
            n_sup += len(supported)
            n_spu += len(qc) - len(supported)
        tot = n_sup + n_spu
        pct = (n_spu / tot * 100) if tot else 0.0
        print(f"[kg-5d noise] {tot} query constraints: {n_sup} gold-supported, {n_spu} spurious "
              f"({pct:.0f}% candidate noise, avg {n_spu/len(queries):.2f}/query)", flush=True)
        eff_constraints = cleaned

    # KG-5d: IDF over query constraints -- df = # corpus clauses satisfying the constraint (value_match-aware),
    # idf = log(N/df). Computed once over the distinct constraints actually used (props_of is cached).
    idf: dict = {}
    if MATCH == "idf":
        import math

        N = len(corpus_ids)
        distinct = set().union(*(eff_constraints[q.query_id] for q in queries)) if queries else set()
        for c in distinct:
            df = sum(1 for cid in corpus_ids if constraint_match_count({c}, props_of(cid)) >= 1)
            idf[c] = math.log(N / df) if df else math.log(N)
        print(f"[kg-5d idf] weighted {len(distinct)} distinct constraints over N={N} clauses", flush=True)

    def _match(qid, props) -> float:
        if RANK == "dense":  # KG-6 dense-only baseline: no constraint match, rank by embedding tiebreak alone
            return 0.0
        qc = eff_constraints[qid]
        if MATCH == "idf":
            return sum(idf[c] for c in qc if constraint_match_count({c}, props) >= 1)
        return float(constraint_match_count(qc, props))

    def _tiebreak(qid, c):
        if VARIANT in ("v4", "v3") and c in clause_emb:
            return -cosine(query_emb[qid], clause_emb[c])
        return 0.0

    def _typematch(qid, c):
        # C (hybrid): 0 if the clause's function is in the query's top-2 (sorts first), else 1 -- a SOFT boost
        # applied AFTER the constraint match, so it only reorders equal-constraint clauses (never overrides it).
        if MODE == "hybrid" and clause_fn.get(c, set()) & {f for f in qfuncs[qid] if f != "NONE"}:
            return 0
        return 1

    # phase 1: KG graded rank (constraint match primary; hybrid type-boost; embedding/arbitrary tiebreak)
    ranked_of: dict[str, list] = {}
    for q in queries:
        pool = pool_of(q)
        ranked_of[q.query_id] = sorted(
            pool, key=lambda c: (-_match(q.query_id, props_of(c)),
                                 _typematch(q.query_id, c), _tiebreak(q.query_id, c), c)
        )

    # phase 2 (V3 only): one listwise Gemma call re-orders each query's top-K (concurrent, +1 call/query)
    if VARIANT == "v3":
        from eval.listwise_rerank import Ranking, _listwise_prompt
        from rag_wright.models.profiles import profile_for
        from rag_wright.models.seam import build_model

        lmodel = os.environ.get("RERANK_MODEL", "google/gemma-4-31b-it")
        K = int(os.environ.get("LISTWISE_N", "25"))
        prof = profile_for(lmodel)
        skw = {"method": prof.structured_method}
        if prof.structured_extra_body is not None:
            skw["extra_body"] = prof.structured_extra_body
        lrun = build_model(lmodel, timeout=40.0, max_retries=1).with_structured_output(Ranking, **skw)

        def _listwise(q):
            win = ranked_of[q.query_id][:K]
            if len(win) < 2:
                return ranked_of[q.query_id]
            try:
                order = lrun.invoke(_listwise_prompt(q.text, [corpus.get(c, "") for c in win])).order
            except Exception:  # noqa: BLE001 - keep the KG order on a listwise failure
                return ranked_of[q.query_id]
            seen, new = set(), []
            for i in order:
                if 1 <= i <= len(win) and (i - 1) not in seen:
                    seen.add(i - 1)
                    new.append(win[i - 1])
            new += [c for j, c in enumerate(win) if j not in seen]  # any dropped, appended in KG order
            return new + ranked_of[q.query_id][K:]

        print(f"[listwise] {len(queries)} queries, top-{K} re-order via {lmodel}", flush=True)
        reordered = map_concurrent(queries, _listwise, max_concurrency=8, label="[listwise]", echo=True)
        ranked_of = {q.query_id: r for q, r in zip(queries, reordered)}

    r10, r20, ndcg = [], [], []
    # KG-6: bucket by query hardness (# extracted constraints) to test whether the typed-KG advantage scales
    buckets: dict[str, list] = {"0 (KG-inert)": [], "1 (single)": [], ">=2 (multi)": []}
    for q in queries:
        ranked = ranked_of[q.query_id]
        cond = [c for c in ranked if c in q.graded]
        m = (recall_at_k(cond, q.relevant, 10), recall_at_k(cond, q.relevant, 20),
             ndcg_at_k(cond, q.graded, 10))
        r10.append(m[0]); r20.append(m[1]); ndcg.append(m[2])
        n = len(constraints[q.query_id])
        buckets["0 (KG-inert)" if n == 0 else "1 (single)" if n == 1 else ">=2 (multi)"].append(m)

    tb = "embedding-cosine" if VARIANT in ("v4", "v3") else "arbitrary"
    calls = "2 (query extract + 1 listwise, no per-clause reranker)" if VARIANT == "v3" else \
            "1 (query extract; no reranker)"
    print(f"\n=== KG-PRIMARY {VARIANT.upper()} (graded rank, {tb} tiebreak{'+listwise' if VARIANT=='v3' else ''})  "
          f"queries={len(queries)}  MODE={MODE}  RANK={RANK}  LLM calls/query = {calls} ===", flush=True)
    print(f"  recall@10 = {statistics.mean(r10):.3f}", flush=True)
    print(f"  recall@20 = {statistics.mean(r20):.3f}", flush=True)
    print(f"  nDCG@10   = {statistics.mean(ndcg):.3f}", flush=True)
    print(f"  -- KG-6 by query hardness (RANK={RANK}) --", flush=True)
    for name, ms in buckets.items():
        if ms:
            print(f"  [{name:14s} n={len(ms):2d}]  r@10={statistics.mean(m[0] for m in ms):.3f}  "
                  f"r@20={statistics.mean(m[1] for m in ms):.3f}  nDCG@10={statistics.mean(m[2] for m in ms):.3f}",
                  flush=True)
    store.close()


if __name__ == "__main__":
    main()
