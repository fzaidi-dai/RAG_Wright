"""(b) improvement experiments: variants of the ONE listwise Gemma call, each measured vs the current (b)
baseline (0.671/0.576) and pointwise-Gemma (0.737/0.758). Variants: baseline (permutation), cot (reason then
rank), score (per-candidate 0-1 score), feat (KG features annotated per candidate in the prompt), + a K-sweep.
First stage = (a) LegalBERT hard-neg CE. Reuses pools/bge/disc/features caches. Crash-safe per (variant,qid).

  HF_HUB_OFFLINE=1 uv run --no-sync python -m scripts.distill.listwise_variants
"""
from __future__ import annotations

import json
import statistics
import threading
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from eval.acord import load_corpus, load_test_queries
from eval.acord_retrieval import ndcg_at_k
from eval.harness import recall_at_k
from eval.listwise_rerank import Ranking, _listwise_prompt
from rag_wright.models.profiles import profile_for
from rag_wright.models.seam import build_model
from rag_wright.util.concurrent import map_concurrent

OUT = Path("data/models/ce")
MODEL = "google/gemma-4-31b-it"
CONTRA = {
    "seller-favorable cap on liability clauses", "mutual liability cap", "unilateral liability cap",
    "buyer-favorable warranty disclaimer clauses", "seller-favorable indemnification clauses",
    "mutual indemnification provisions", "unilateral indemnification clause", "mutual indirect damages waiver",
    "unilateral indirect damages waiver", "buyer-favorable waiver of indirect damages clauses",
}


class RankingCoT(BaseModel):
    reasoning: str  # brief per-candidate reasoning, emitted BEFORE the ranking
    order: list[int]


class Scores(BaseModel):
    scores: list[float]  # one 0.00-1.00 score per candidate, SAME order as listed


def _cot_prompt(test, clauses, feats=None):
    body = "\n".join(f"[{i + 1}] {c[:600]}" for i, c in enumerate(clauses))
    return ("You are a contract attorney ranking clauses by how well each satisfies this decisive test:\n"
            f"{test}\n\nFor EACH candidate, briefly reason whether it satisfies the test -- paying attention to "
            "favorability/mutuality/carve-out nuance that separates a true match from a same-type near-miss. "
            "THEN output the candidate numbers ordered best-to-worst.\n\n"
            f"Candidates:\n{body}\n\nInclude ALL {len(clauses)} numbers exactly once, best first.")


def _score_prompt(test, clauses, feats=None):
    body = "\n".join(f"[{i + 1}] {c[:600]}" for i, c in enumerate(clauses))
    return ("You are a contract attorney. Score each candidate clause from 0.00 to 1.00 for how FULLY it "
            f"satisfies this decisive test (use fine gradations; a same-type clause whose decisive part is "
            f"partial/arguable scores lower):\n{test}\n\nOutput one score per candidate IN THE SAME ORDER as "
            f"listed ({len(clauses)} scores).\n\nCandidates:\n{body}")


def _feat_prompt(test, clauses, feats):
    body = "\n".join(f"[{i + 1}] <{feats[i]}> {c[:520]}" for i, c in enumerate(clauses))
    return ("You are a contract attorney ranking clauses by how well each satisfies this decisive test:\n"
            f"{test}\n\nEach candidate is annotated with its extracted structural features in <>. Use them "
            "plus the text to rank best-to-worst.\n\n"
            f"Candidates:\n{body}\n\nOutput ALL {len(clauses)} numbers exactly once, best first.")


def _score_feat_prompt(test, clauses, feats):  # STACK: per-candidate scoring + features in prompt
    body = "\n".join(f"[{i + 1}] <{feats[i]}> {c[:520]}" for i, c in enumerate(clauses))
    return ("You are a contract attorney. Each candidate is annotated with its extracted structural features "
            "in <>. Score each candidate from 0.00 to 1.00 for how FULLY it satisfies this decisive test (use "
            f"fine gradations; a same-type clause whose decisive part is partial/arguable scores lower):\n{test}\n\n"
            f"Use the features plus the text. Output one score per candidate IN THE SAME ORDER as listed "
            f"({len(clauses)} scores).\n\nCandidates:\n{body}")


def _score_order(v, k):
    return [i + 1 for i, _ in sorted(enumerate(v.scores[:k]), key=lambda x: -x[1])]


def _base_prompt(test, clauses, feats=None):  # 3-arg wrapper over the imported 2-arg prompt
    return _listwise_prompt(test, clauses)


VARIANTS = {  # name -> (schema, prompt_fn, order_fn, K, use_feats)
    "baseline_k40": (Ranking, _base_prompt, lambda v, k: v.order, 40, False),
    "cot_k40": (RankingCoT, _cot_prompt, lambda v, k: v.order, 40, False),
    "score_k40": (Scores, _score_prompt,
                  lambda v, k: [i + 1 for i, _ in sorted(enumerate(v.scores[:k]), key=lambda x: -x[1])], 40, False),
    "feat_k40": (Ranking, _feat_prompt, lambda v, k: v.order, 40, True),
    "baseline_k25": (Ranking, _base_prompt, lambda v, k: v.order, 25, False),
    "baseline_k60": (Ranking, _base_prompt, lambda v, k: v.order, 60, False),
    # STACK experiments: combine the winners (scoring + K=25 + features)
    "score_k25": (Scores, _score_prompt, _score_order, 25, False),
    "feat_k25": (Ranking, _feat_prompt, lambda v, k: v.order, 25, True),
    "score_feat_k40": (Scores, _score_feat_prompt, _score_order, 40, True),
    "score_feat_k25": (Scores, _score_feat_prompt, _score_order, 25, True),
}


def main() -> None:
    load_dotenv()
    corpus = {c.clause_id: c.text for c in load_corpus()}
    qmap = {q.query_id: q for q in load_test_queries()}
    pools = json.loads((OUT / "pools.json").read_text())
    disc_of = json.loads(Path("data/models/condensed_disc.json").read_text())
    feat_of = {}
    for line in (OUT / "clause_features.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            feat_of[r["clause_id"]] = r["feat"]
    ce_a = defaultdict(dict)
    for line in (OUT / "preds_legalbert_hard.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            ce_a[r["query_id"]][r["clause_id"]] = r["score"]
    bge = defaultdict(dict)
    for line in (OUT / "bge_scores.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            bge[r["qid"]][r["clause"]] = r["bge"]
    gp = defaultdict(dict)
    for line in Path("data/models/condensed_scores.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            gp[r["qid"]][r["clause"]] = r["score"]

    prof = profile_for(MODEL)
    skw = {"method": prof.structured_method}
    if prof.structured_extra_body is not None:
        skw["extra_body"] = prof.structured_extra_body

    def run_variant(name):
        schema, prompt_fn, order_fn, k, use_feats = VARIANTS[name]
        runner = build_model(MODEL, timeout=45.0, max_retries=1).with_structured_output(schema, **skw)
        cache = OUT / f"lw_{name}.jsonl"
        done, lock = {}, threading.Lock()
        if cache.exists():
            for line in cache.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    done[r["qid"]] = r["order"]

        def one(qid):
            first = sorted(pools[qid], key=lambda c: -ce_a[qid].get(c, 0.0))
            topk = first[:k]
            if len(topk) <= 1:
                return qid, first
            if qid in done:
                order = done[qid]
            else:
                test = disc_of.get(qid, qmap[qid].text)
                feats = [feat_of.get(c, "") for c in topk] if use_feats else None
                order = []
                for _ in range(3):
                    try:
                        v = runner.invoke(prompt_fn(test, [corpus.get(c, "") for c in topk], feats))
                    except Exception:  # noqa: BLE001
                        continue
                    if v is not None:
                        order = order_fn(v, len(topk))
                        break
                with lock:
                    if order:
                        with cache.open("a", encoding="utf-8") as f:
                            f.write(json.dumps({"qid": qid, "order": order}) + "\n")
                            f.flush()
                        done[qid] = order
            seen = []
            for i in order:
                if 1 <= i <= len(topk) and topk[i - 1] not in seen:
                    seen.append(topk[i - 1])
            for c in topk:
                if c not in seen:
                    seen.append(c)
            return qid, seen + [c for c in first if c not in set(topk)]

        qids = [q for q in pools if pools[q]]
        return dict(map_concurrent(qids, one, max_concurrency=8, label=f"[{name}]", echo=True, every=10))

    def stats(order_of):
        rows = {}
        for q in qmap.values():
            cs = pools[q.query_id]
            if cs:
                o = order_of(q.query_id, cs)
                rows[q.text] = (ndcg_at_k(o, q.graded, 10), recall_at_k(o, q.relevant, 10), recall_at_k(o, q.relevant, 20))
        return rows

    def mean(rows, i, subset=None):
        vals = [v[i] for t, v in rows.items() if subset is None or t in subset]
        return statistics.mean(vals) if vals else 0.0

    results = {
        "(a) CE hard": stats(lambda qid, cs: sorted(cs, key=lambda c: -ce_a[qid].get(c, 0.0))),
        "gemma pointwise": stats(lambda qid, cs: sorted(cs, key=lambda c: -gp[qid].get(c, 0.0))),
    }
    for name in VARIANTS:
        finals = run_variant(name)
        results[name] = stats(lambda qid, cs, f=finals: f[qid])

    print("\n=== listwise (b) variants | nDCG@10 / recall@10 / recall@20 ===", flush=True)
    print(f"  {'method':<18}{'ndcg(all)':<11}{'r20(all)':<10}{'ndcg(con)':<11}{'r10(con)':<10}{'r20(con)':<10}", flush=True)
    for name, rows in results.items():
        print(f"  {name:<18}{mean(rows, 0):<11.3f}{mean(rows, 2):<10.3f}{mean(rows, 0, CONTRA):<11.3f}"
              f"{mean(rows, 1, CONTRA):<10.3f}{mean(rows, 2, CONTRA):<10.3f}", flush=True)


if __name__ == "__main__":
    main()
