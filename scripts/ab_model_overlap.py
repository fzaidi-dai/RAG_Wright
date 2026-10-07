"""Misclassification-overlap / oracle / ensemble analysis over the 4 A/B models' persisted holdout preds
(data/models/ab_preds/*.json, each with `hpreds` = per-span predicted label aligned to build_holdout_spans()).
Answers: do the models err on the SAME spans (a shared ceiling) or DIFFERENT ones (ensemble/routing upside)?
Reports single-model recall, majority-vote + oracle (any-correct) ensemble recall, pairwise error-overlap, and
per-class where each model uniquely wins -- read-only, no Modal, no retrain."""

from __future__ import annotations

import collections
import json
from pathlib import Path

from dotenv import load_dotenv

from rag_wright.packs.contracts.schemas.function import canonical_function
from scripts.train_legalbert_function import build_holdout_spans

load_dotenv("/Users/farhan/work/RAG_Wright/.env")
AB = Path("data/models/ab_preds")
SHORT = {"nlpaueb/legal-bert-base-uncased": "legal-bert",
         "nlpaueb/bert-base-uncased-contracts": "contracts-bert",
         "microsoft/deberta-v3-base": "deberta-v3", "Qwen/Qwen2.5-0.5B": "qwen-0.5B"}


def _mean_recall(correct_by_class):  # {class: [hits, n]} -> mean per-class recall
    recs = {k: h / n for k, (h, n) in correct_by_class.items() if n}
    return sum(recs.values()) / len(recs) if recs else 0.0, recs


def main():
    _, gold_raw, _ = build_holdout_spans()
    gold = [canonical_function(g) or g for g in gold_raw]
    n = len(gold)
    # non-NONE evaluable indices (the A/B metric masks NONE gold)
    idx = [i for i, g in enumerate(gold) if g != "NONE"]

    models = {}
    for f in sorted(AB.glob("*.json")):
        d = json.loads(f.read_text())
        base = d["base"]
        hp = d["hpreds"]
        assert len(hp) == n, f"{base}: {len(hp)} preds != {n} gold"
        models[SHORT.get(base, base)] = [canonical_function(p) or p for p in hp]
    names = list(models)
    print(f"holdout spans={n}  non-NONE evaluable={len(idx)}  models={names}\n", flush=True)

    # per-model mean recall (sanity vs the eval table)
    print("=== single-model mean non-NONE recall ===", flush=True)
    per_model_rec = {}
    for m, preds in models.items():
        by = collections.defaultdict(lambda: [0, 0])
        for i in idx:
            by[gold[i]][1] += 1
            by[gold[i]][0] += (preds[i] == gold[i])
        mr, recs = _mean_recall(by)
        per_model_rec[m] = recs
        print(f"   {mr:.3f}  {m}", flush=True)

    # ORACLE (any model correct) + MAJORITY vote, as mean per-class recall
    oracle_by = collections.defaultdict(lambda: [0, 0])
    maj_by = collections.defaultdict(lambda: [0, 0])
    for i in idx:
        g = gold[i]
        preds_i = [models[m][i] for m in names]
        oracle_by[g][1] += 1
        oracle_by[g][0] += any(p == g for p in preds_i)
        vote = collections.Counter(preds_i).most_common(1)[0][0]  # ties -> first-seen most common
        maj_by[g][1] += 1
        maj_by[g][0] += (vote == g)
    oracle_mr, _ = _mean_recall(oracle_by)
    maj_mr, _ = _mean_recall(maj_by)
    best_single = max(sum(r.values()) / len(r) for r in per_model_rec.values())
    print(f"\n=== ensemble (mean non-NONE recall) ===", flush=True)
    print(f"   best single model : {best_single:.3f}", flush=True)
    print(f"   majority vote      : {maj_mr:.3f}", flush=True)
    print(f"   ORACLE (any right) : {oracle_mr:.3f}   <- upper bound if a perfect router existed", flush=True)
    print(f"   -> ensemble/routing headroom over best single: {oracle_mr - best_single:+.3f}", flush=True)

    # pairwise ERROR overlap: of spans where BOTH err, vs either errs (Jaccard of error sets)
    print(f"\n=== pairwise error overlap (Jaccard of wrong-span sets; high => same mistakes) ===", flush=True)
    errs = {m: {i for i in idx if models[m][i] != gold[i]} for m in names}
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            ma, mb = names[a], names[b]
            inter = len(errs[ma] & errs[mb])
            union = len(errs[ma] | errs[mb])
            print(f"   {ma:<15} vs {mb:<15} both-wrong={inter:<4} either-wrong={union:<4} "
                  f"Jaccard={inter/union:.2f}", flush=True)
    common_all = set.intersection(*errs.values())
    any_err = set.union(*errs.values())
    print(f"\n   spans ALL 4 get wrong: {len(common_all)}   spans ANY gets wrong: {len(any_err)}   "
          f"(irreducible share = {len(common_all)/len(any_err):.0%})", flush=True)

    # per-class: which model is uniquely best (routing candidates) -- classes with a big spread
    print(f"\n=== per-class recall spread (classes where models most disagree; routing candidates) ===", flush=True)
    classes = sorted({gold[i] for i in idx})
    rows = []
    for c in classes:
        rr = {m: per_model_rec[m].get(c, 0.0) for m in names}
        spread = max(rr.values()) - min(rr.values())
        best_m = max(rr, key=rr.get)
        n_c = sum(1 for i in idx if gold[i] == c)
        rows.append((spread, c, n_c, best_m, rr))
    for spread, c, n_c, best_m, rr in sorted(rows, reverse=True)[:14]:
        detail = " ".join(f"{m.split('-')[0]}={rr[m]:.2f}" for m in names)
        print(f"   {c:<34} n={n_c:<4} spread={spread:.2f} best={best_m:<14} {detail}", flush=True)


if __name__ == "__main__":
    main()
