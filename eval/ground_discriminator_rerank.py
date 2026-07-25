"""T58b grounding: decomposer(discriminator) + LLM reranker on ONE pool, with clean attribution.

Blind generation, held-out scoring: the decomposer sees only the query; the reranker sees only
(test, clause); gold is revealed only to score. Conditions on the same pool:
  C0  BGE cross-encoder + raw query            (baseline)
  C1  LLM grade + raw query                    (LLM vs BGE)
  C2a LLM grade + BLIND decomposer discriminator (the real system)
  C2b LLM grade + ORACLE discriminator          (upper bound / decomposer attribution)
Metrics: recall@50, grade-3-vs-gold (does the test separate gold from near-miss distractors?), gold-rank
distribution, and readable judgments. Reranker model is swappable (RERANK_MODEL); starts on Flash.
Pointwise, temperature 0, throughput-routed, progress FLUSHED + echoed (data/models/ground_progress.log).

  uv run python -m eval.ground_discriminator_rerank
  RERANK_MODEL=deepseek/deepseek-v4-pro uv run python -m eval.ground_discriminator_rerank   # swap model
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from rag_wright.capabilities.reranking import BGEReranker
from rag_wright.models.profiles import profile_for
from rag_wright.models.seam import build_model
from rag_wright.store.arcadedb import SPAN_TYPE, ArcadeDBStore, _str_array
from rag_wright.util.concurrent import map_concurrent

QUERY = os.environ.get("QUERY", "mutual liability cap")
FUNCTION = os.environ.get("FUNCTION", "Uncapped Liability")
DB = os.environ.get("PIVOT_DB", "ragwright_acord_pivot")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "deepseek/deepseek-v4-flash")
DECOMP_MODEL = os.environ.get("DECOMP_MODEL", "deepseek/deepseek-v4-flash")
ORACLE = os.environ.get(
    "ORACLE_DISCRIMINATOR",
    "The clause must (a) cap total liability at a maximum AMOUNT or formula (a ceiling), NOT merely exclude "
    "indirect/consequential damages; AND (b) apply to BOTH parties (mutual), not one-sided.",
)
PROGRESS = Path("data/models/ground_progress.log")


class Grade(BaseModel):
    score: float  # 0.00-1.00 CONTINUOUS relevance to the test (the reranker's own judgment of degree)
    why: str


def _progress(msg: str) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(msg + "\n", encoding="utf-8")
    print(msg, flush=True)


def _decompose_prompt(query: str) -> str:
    return (
        "Rewrite this legal clause-search query as the decisive test a clause must pass to MATCH -- the "
        "specific criterion that separates a match from other clauses of the same TYPE.\n"
        "Rules (follow strictly):\n"
        "- Capture ONLY the conditions the query actually states. Do NOT add stricter or extra conditions it "
        "does not say -- in particular do NOT invent 'equal/identical amounts', 'symmetrical', 'no "
        "exceptions', or 'explicitly stated' unless the query says so.\n"
        "- Read each term at its BROADEST faithful meaning. E.g. 'mutual' = applies to BOTH parties (the two "
        "sides need NOT be equal or identical); a 'carve-out/exception to X' = that item is excepted from X. "
        "A clause STILL matches if it also has extra features, asymmetries, or exceptions the query never "
        "mentioned.\n"
        "- Be compound ONLY if the query itself names more than one condition; otherwise keep it single.\n\n"
        f"Query: '{query}'\nOutput ONLY the test, 1-2 sentences."
    )


def _grade_prompt(test: str, clause: str) -> str:
    return (
        "You are a paralegal screening contract clauses. A clause MATCHES only if it satisfies this test:\n"
        f"{test}\n\nScore how fully the clause satisfies the test on a CONTINUOUS 0.00-1.00 scale. Use the full "
        "range and FINE gradations (e.g. 0.97, 0.83, 0.41, 0.12) to reflect degree -- avoid round numbers and "
        "ties:\n 1.00 = unambiguously and fully satisfies the decisive test\n"
        " ~0.50 = same clause family but the decisive part is partial / arguable\n 0.00 = unrelated\n"
        "Give the score and a one-line reason quoting the operative span.\n\n"
        f"Clause:\n{clause[:1500]}"
    )


def main() -> None:
    load_dotenv()
    store = ArcadeDBStore.from_env(database=DB, reset=False)
    corpus = {c.clause_id: c.text for c in load_corpus()}
    q = next(x for x in load_test_queries() if x.text.strip().lower() == QUERY.lower())
    gold = set(q.relevant)
    pool = list(dict.fromkeys(r["parent_okf_path"] for r in store._query(
        f"SELECT parent_okf_path FROM {SPAN_TYPE} WHERE function IN {_str_array([FUNCTION])}")))
    _progress(f"[setup] query='{QUERY}' function='{FUNCTION}' pool={len(pool)} gold_in_pool={len(gold & set(pool))}/{len(gold)}  reranker={RERANK_MODEL}")

    disc = build_model(DECOMP_MODEL, temperature=0.0).invoke(_decompose_prompt(QUERY)).content.strip()
    print(f"\nBLIND discriminator: {disc}\nORACLE discriminator: {ORACLE}\n", flush=True)

    bge = dict(zip(pool, BGEReranker().score(QUERY, [corpus.get(c, "") for c in pool])))

    # Build the structured runnable with FAST-FAIL (25s timeout, no client retries) so one hung call can
    # never wedge the whole batch via gather (the tail-latency trap); mirror build_structured's profile use.
    _prof = profile_for(RERANK_MODEL)
    _skw = {"method": _prof.structured_method}
    if _prof.structured_extra_body is not None:
        _skw["extra_body"] = _prof.structured_extra_body
    runnable = build_model(RERANK_MODEL, timeout=25.0, max_retries=0).with_structured_output(Grade, **_skw)

    def grade(test: str, clause: str) -> Grade:
        for _ in range(3):
            try:
                v = runnable.invoke(_grade_prompt(test, clause))
            except Exception:  # noqa: BLE001
                continue
            if v is not None:
                return Grade(score=max(0.0, min(1.0, float(v.score))), why=v.why)
        return Grade(score=0.0, why="(no result)")

    # Crash-safe + resumable: each grade is appended (flushed) to GRADE_CACHE the moment it completes,
    # keyed by (model, exact test text, clause) -- so a killed/failed run loses nothing, a re-run skips
    # what's done, and a changed discriminator (different test) re-grades correctly. FRESH_GRADES=1 clears it.
    cache = Path("data/models/ground_scores.jsonl")  # continuous-score cache (distinct from the 0-3 grades)
    cache.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("FRESH_GRADES") == "1":
        cache.unlink(missing_ok=True)
    lock = threading.Lock()
    done: dict[tuple[str, str, str], Grade] = {}
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[(r["model"], r["test"], r["clause_id"])] = Grade(score=r["score"], why=r["why"])
    _progress(f"[resume] {len(done)} scores cached")

    def grade_pool(label: str, test: str) -> dict[str, Grade]:
        def one(c: str) -> Grade:
            key = (RERANK_MODEL, test, c)
            if key in done:
                return done[key]
            g = grade(test, corpus.get(c, ""))
            with lock:  # append+flush per clause -> durable; serialize the file write
                with cache.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"model": RERANK_MODEL, "test": test, "clause_id": c,
                                        "score": g.score, "why": g.why}) + "\n")
                    f.flush()
                done[key] = g
            return g

        res = map_concurrent(pool, one, max_concurrency=8, progress_path=PROGRESS, label=label, echo=True, every=5)
        return dict(zip(pool, res))

    conds = {
        "C2a llm+blinddisc": grade_pool("[C2a]", disc),
        "C2b llm+oracledisc": grade_pool("[C2b]", ORACLE),
    }

    def rank(scoref) -> list[str]:
        return sorted(pool, key=lambda c: (-scoref(c), -bge[c]))

    judged_in_pool = [c for c in pool if c in q.graded]

    def report(name: str, order: list[str]) -> None:
        # CONDENSED = remove un-judged from the ranked list, then score what ACORD actually rated
        cond = [c for c in order if c in q.graded]
        print(f"{name}: full_recall@50={recall_at_k(order, gold, 50):.3f}  ||  "
              f"CONDENSED nDCG@10={ndcg_at_k(cond, q.graded, 10):.3f}  "
              f"recall@10={recall_at_k(cond, gold, 10):.3f}  recall@20={recall_at_k(cond, gold, 20):.3f}", flush=True)

    print(f"\n=== {QUERY} | pool={len(pool)} gold={len(gold)} judged_in_pool={len(judged_in_pool)} | reranker={RERANK_MODEL} ===", flush=True)
    report("C0  BGE+rawquery  ", rank(lambda c: bge[c]))
    for name, gmap in conds.items():
        order = rank(lambda c, m=gmap: m[c].score)
        strong = [c for c in pool if gmap[c].score >= 0.8]
        report(name, order)
        print(f"     strong(score>=0.8)={len(strong)} (gold {len(set(strong) & gold)}/{len(gold)})", flush=True)

    # qualitative: the oracle-condition judgments on 3 gold + 3 top-scoring distractors
    gmap = conds["C2b llm+oracledisc"]
    print("\n[read C2b] gold judgments:", flush=True)
    for g in list(gold & set(pool))[:3]:
        print(f"  gold score={gmap[g].score:.2f}: {gmap[g].why[:150]}", flush=True)
    top_distractors = [c for c in rank(lambda c: gmap[c].score) if c not in gold][:3]
    print("[read C2b] top non-gold judgments:", flush=True)
    for c in top_distractors:
        print(f"  distractor score={gmap[c].score:.2f}: {gmap[c].why[:150]}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
