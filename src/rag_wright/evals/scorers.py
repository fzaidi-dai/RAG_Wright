"""EV-3: the scorers, one per capability kind (the `creating-evals` skill's method, as code).

A scorer is a pure function of the gold set and the predictions (`{item id: output}`) that returns a `ScorerResult`:
the GATED measures against their thresholds, and DIAGNOSTICS that are reported and never gated. A gold item with no
prediction is an error -- counted wrong, and reported apart (ADR-0133: a failed call is never an answer).

  score_classification   1. classification / typed decision: per-class recall@k against a floor (the weakest class,
                            never the average); top-1 accuracy too when something acts on the main label.
  score_retrieval        2. retrieval: mean recall@k (relevant = grade at or above a floor) as the gate; graded nDCG@k
                            (exponential gain) reported, never gated.
  score_relational       6. graph / relational: recall over the answer set (reachability, not ranking), broken down
                            by a metadata key; over-reach reported as precision.
  score_candidate_choice 8. candidate-then-choose: the generator's coverage of the expected values (it caps recall)
                            and value-level recall and precision of the choices (emitting more is not a win).

Each is verified against an engine eval's published numbers (`tests/evals/test_scorer_parity.py`).
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Callable, Collection, Mapping, Optional, Sequence, Union

from rag_wright.contracts.evals import GoldItem, GoldSet, ScorerResult

Gold = Union[GoldSet, Sequence[GoldItem]]


def _items(gold: Gold) -> list[GoldItem]:
    return list(gold.items if isinstance(gold, GoldSet) else gold)


def _ranked(prediction: Any) -> list[str]:
    """A prediction as labels, best first: one label, a ranked list, or `{label: score}`."""
    if prediction is None:
        return []
    if isinstance(prediction, Mapping):
        return [str(k) for k, _v in sorted(prediction.items(), key=lambda kv: -float(kv[1]))]
    if isinstance(prediction, str):
        return [prediction]
    return [str(p) for p in prediction]


def _labels(expected: Any) -> list[str]:
    return [expected] if isinstance(expected, str) else [str(e) for e in expected]


def score_classification(gold: Gold, predictions: Mapping[str, Any], *, floor: float, k: int = 1,
                         top1_floor: Optional[float] = None, exclude: Collection[str] = (),
                         name: str = "classification") -> ScorerResult:
    """Scorer 1. `expected`: a label, or several (multi-label). A prediction: a label, a ranked list or
    `{label: score}`. Gate: every class's recall@k reaches `floor` (classes in `exclude`, e.g. a no-label class, are
    reported but not gated) and, with `top1_floor`, top-1 accuracy reaches it. Diagnostics: per-class recall@k,
    their mean, top-1 accuracy, tags per item (the precision cost of k), confusion pairs (gold vs top prediction),
    errors."""
    items = _items(gold)
    hits: dict[str, list[int]] = {}
    confusions: Counter = Counter()
    top1 = tags = errors = 0
    for item in items:
        ranked = _ranked(predictions.get(item.id))
        if item.id not in predictions:
            errors += 1
        wanted = _labels(item.expected)
        top = ranked[0] if ranked else None
        top1 += top in wanted
        tags += min(k, len(ranked))
        for label in wanted:
            row = hits.setdefault(label, [0, 0])
            row[1] += 1
            row[0] += label in ranked[:k]
        if top is not None and top not in wanted:
            confusions[(wanted[0], top)] += 1
    per_class = {label: {"n": n, "recall": h / n} for label, (h, n) in hits.items()}
    gated = [v["recall"] for label, v in per_class.items() if label not in exclude]
    key = f"min_class_recall@{k}"
    gate: dict[str, Optional[float]] = {key: min(gated) if gated else None}
    thresholds = {key: floor}
    accuracy = top1 / len(items) if items else None
    if top1_floor is not None:
        gate["top1_accuracy"] = accuracy
        thresholds["top1_accuracy"] = top1_floor
    passed = all(v is not None and v >= thresholds[m] for m, v in gate.items())
    return ScorerResult(scorer=name, passed=passed, gate=gate, thresholds=thresholds, diagnostics={
        "n": len(items), "errors": errors, "per_class": per_class,
        f"mean_class_recall@{k}": sum(gated) / len(gated) if gated else None,
        "top1_accuracy": accuracy, "tags_per_item": tags / len(items) if items else None,
        "confusions": [{"gold": g, "predicted": p, "count": n}
                       for (g, p), n in sorted(confusions.items(), key=lambda kv: (-kv[1], kv[0]))][:50]})


def _graded(expected: Any) -> dict[str, int]:
    """Expected relevance as `{id: grade}`: a graded map as is, a plain list of ids as grade 1."""
    if isinstance(expected, Mapping):
        return {str(k): int(v) for k, v in expected.items()}
    return {str(i): 1 for i in expected}


def _ndcg(ranked: Sequence[str], graded: Mapping[str, int], k: int) -> float:
    """Graded nDCG@k, exponential gain (2^grade - 1), ideal over every graded id."""
    dcg = sum((2.0 ** graded.get(cid, 0) - 1.0) / math.log2(r + 1) for r, cid in enumerate(ranked[:k], start=1))
    ideal = sorted(graded.values(), reverse=True)[:k]
    idcg = sum((2.0 ** g - 1.0) / math.log2(r + 1) for r, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg > 0 else 0.0


def score_retrieval(gold: Gold, predictions: Mapping[str, Any], *, k: int, recall: float,
                    relevant_grade: int = 1, ndcg_k: int = 10, also_recall_at: Sequence[int] = (),
                    name: str = "retrieval") -> ScorerResult:
    """Scorer 2. `expected`: the relevant ids, or `{id: grade}`. A prediction: ids, best first. Gate: mean recall@k,
    relevant = grade >= `relevant_grade`. Diagnostics: graded nDCG@`ndcg_k` (never gated), recall at each of
    `also_recall_at`, per-item recall@k. An item with nothing relevant is left out of the means and listed in
    `no_relevant` (recall is undefined, not perfect)."""
    items = _items(gold)
    per_item: dict[str, float] = {}
    extra: dict[int, list[float]] = {kk: [] for kk in also_recall_at}
    ndcgs: list[float] = []
    no_relevant, errors = [], 0
    for item in items:
        graded = _graded(item.expected)
        relevant = {cid for cid, g in graded.items() if g >= relevant_grade}
        if item.id not in predictions:
            errors += 1
        if not relevant:
            no_relevant.append(item.id)
            continue
        ranked = [str(c) for c in (predictions.get(item.id) or [])]
        per_item[item.id] = len(set(ranked[:k]) & relevant) / len(relevant)
        for kk in also_recall_at:
            extra[kk].append(len(set(ranked[:kk]) & relevant) / len(relevant))
        ndcgs.append(_ndcg(ranked, graded, ndcg_k))
    mean = (lambda xs: sum(xs) / len(xs) if xs else None)  # noqa: E731
    gate = {f"recall@{k}": mean(list(per_item.values()))}
    thresholds = {f"recall@{k}": recall}
    passed = all(v is not None and v >= thresholds[m] for m, v in gate.items())
    return ScorerResult(scorer=name, passed=passed, gate=gate, thresholds=thresholds, diagnostics={
        "n": len(items), "scored": len(per_item), "errors": errors, "no_relevant": no_relevant,
        f"ndcg@{ndcg_k}": mean(ndcgs), **{f"recall@{kk}": mean(v) for kk, v in extra.items()},
        f"per_item_recall@{k}": per_item})


def score_relational(gold: Gold, predictions: Mapping[str, Any], *, recall: float, k: Optional[int] = None,
                     by: Optional[str] = None, name: str = "relational") -> ScorerResult:
    """Scorer 6. `expected`: the answer set (ids). A prediction: the ids reached (`k` keeps only the first k of a
    ranked answer; None = all). Gate: mean recall over the answer sets. Diagnostics: the mean per value of the
    item metadata key `by` (e.g. hops), precision (reached ids that are answers; over-reach, never gated), the
    answers not reached. An item with an empty answer set is left out and listed in `no_answers`."""
    items = _items(gold)
    per_item: dict[str, float] = {}
    groups: dict[str, list[float]] = {}
    hits = reached_n = errors = 0
    misses, no_answers = [], []
    for item in items:
        answers = [str(a) for a in item.expected]
        if item.id not in predictions:
            errors += 1
        if not answers:
            no_answers.append(item.id)
            continue
        reached = [str(r) for r in (predictions.get(item.id) or [])]
        reached = reached[:k] if k is not None else reached
        found = set(reached) & set(answers)
        per_item[item.id] = len(found) / len(set(answers))
        hits += len(found)
        reached_n += len(set(reached))
        misses += [f"{item.id}: {a}" for a in answers if a not in found]
        if by is not None:
            groups.setdefault(str(item.metadata.get(by)), []).append(per_item[item.id])
    value = sum(per_item.values()) / len(per_item) if per_item else None
    passed = value is not None and value >= recall
    diagnostics: dict[str, Any] = {
        "n": len(items), "scored": len(per_item), "errors": errors, "no_answers": no_answers,
        "precision": hits / reached_n if reached_n else None, "per_item_recall": per_item, "misses": misses[:50]}
    if by is not None:
        diagnostics[f"by_{by}"] = {g: {"n": len(v), "recall": sum(v) / len(v)} for g, v in sorted(groups.items())}
    return ScorerResult(scorer=name, passed=passed, gate={"recall": value}, thresholds={"recall": recall},
                        diagnostics=diagnostics)


def _norm(value: Any) -> str:
    return " ".join(str(value).split()).lower().strip(" .,;:")


def lenient_match(a: Any, b: Any) -> bool:
    """Normalised (spaces collapsed, case and edge punctuation ignored), either one containing the other."""
    x, y = _norm(a), _norm(b)
    return bool(x) and bool(y) and (x in y or y in x)


def score_candidate_choice(gold: Gold, predictions: Mapping[str, Any], *, recall: float, precision: float,
                           coverage: float, match: Callable[[Any, Any], bool] = lenient_match,
                           name: str = "candidate_choice") -> ScorerResult:
    """Scorer 8. `expected`: `{"values": [[key, value], ...], "ignore": [value, ...]}` (`ignore`: values the gold
    cannot settle, dropped from the output before scoring), or just the list of `[key, value]`. A prediction:
    `{"candidates": [value, ...], "values": [[key, value], ...]}`. Gate: coverage (expected values found among the
    candidates), value-level recall (an expected value chosen under its key) and precision (a chosen value that is
    expected under that key). Diagnostics: the counts, values per item, coverage misses, errors."""
    items = _items(gold)
    rec_hits = n_expected = prec_hits = n_emitted = covered = errors = 0
    misses: list[str] = []
    for item in items:
        exp = item.expected if isinstance(item.expected, Mapping) else {"values": item.expected}
        truth = [(str(kk), v) for kk, v in exp.get("values", [])]
        ignore = list(exp.get("ignore", []))
        pred = predictions.get(item.id)
        if pred is None:
            errors += 1
            pred = {}
        cands = list(pred.get("candidates", []))
        out = [(str(kk), v) for kk, v in pred.get("values", []) if not any(match(v, a) for a in ignore)]
        n_expected += len(truth)
        n_emitted += len(out)
        for kk, v in truth:
            rec_hits += any(ok == kk and match(ov, v) for ok, ov in out)
            if any(match(c, v) for c in cands):
                covered += 1
            else:
                misses.append(f"{item.id}: {v}")
        prec_hits += sum(any(ok == kk and match(ov, v) for kk, v in truth) for ok, ov in out)
    gate = {"coverage": covered / n_expected if n_expected else None,
            "recall": rec_hits / n_expected if n_expected else None,
            "precision": prec_hits / n_emitted if n_emitted else None}
    thresholds = {"coverage": coverage, "recall": recall, "precision": precision}
    passed = all(v is not None and v >= thresholds[m] for m, v in gate.items())
    return ScorerResult(scorer=name, passed=passed, gate=gate, thresholds=thresholds, diagnostics={
        "n": len(items), "errors": errors, "expected_values": n_expected, "emitted_values": n_emitted,
        "recall_hits": rec_hits, "precision_hits": prec_hits, "covered_values": covered,
        "values_per_item": n_emitted / len(items) if items else None, "coverage_misses": misses[:50]})


__all__ = ["lenient_match", "score_candidate_choice", "score_classification", "score_relational",
           "score_retrieval"]
