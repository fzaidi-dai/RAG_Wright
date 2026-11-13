"""The routing evals' scorer: one choice per item, and any of the item's accepted labels is correct (HD-1).

**Why not the engine's `score_classification`.** It was built for multi-label tagging, where several expected
labels are all true at once, so it reads `[A, B]` as "a good tagger finds both". A routing decision takes ONE action,
and sometimes more than one action would have been fine: `[A, B]` here means "A, or B". Same report shape
(`ScorerResult`), so `arun_eval` takes it unchanged.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from rag_wright.api import GoldItem, GoldSet, ScorerResult


def accepted(expected: Any) -> list[str]:
    """An item's accepted labels: its own label first, then any acceptable alternatives."""
    return [expected] if isinstance(expected, str) else list(expected)


def score_choice(gold: GoldSet, predictions: Mapping[str, Any], *, floor: float,
                 name: str = "choice") -> ScorerResult:
    """A prediction is one label. Gate: every label's accuracy -- over the items whose own label it is -- reaches
    `floor`. Diagnostics: per-label accuracy, overall accuracy, how many correct answers were an alternative rather
    than the item's own label, confusions (own label vs the choice), and errors (a missing prediction, counted wrong)."""
    per_label: dict[str, list[int]] = {}
    confusions: Counter = Counter()
    correct = alternatives = errors = 0
    for item in gold.items:
        labels = accepted(item.expected)
        own = labels[0]
        errors += item.id not in predictions
        chose = predictions.get(item.id)
        hit = chose in labels
        row = per_label.setdefault(own, [0, 0])
        row[0] += hit
        row[1] += 1
        correct += hit
        alternatives += hit and chose != own
        if not hit:
            confusions[(own, str(chose))] += 1
    accuracy = {label: hits / n for label, (hits, n) in per_label.items()}
    gate = {"min_label_accuracy": min(accuracy.values()) if accuracy else None}
    return ScorerResult(
        scorer=name, passed=gate["min_label_accuracy"] is not None and gate["min_label_accuracy"] >= floor,
        gate=gate, thresholds={"min_label_accuracy": floor},
        diagnostics={"n": len(gold.items), "errors": errors, "accuracy": correct / len(gold.items),
                     "alternatives": alternatives,
                     "per_label": {label: {"n": per_label[label][1], "accuracy": a} for label, a in accuracy.items()},
                     "confusions": [{"own": o, "chose": c, "count": n}
                                    for (o, c), n in sorted(confusions.items(), key=lambda kv: (-kv[1], kv[0]))]})


def score_workers(gold: GoldSet, predictions: Mapping[str, Any], *, workers: tuple[str, ...], floor: float,
                  name: str = "workers") -> ScorerResult:
    """D2: a brief needs a set of workers (`["none"]` for none), and a prediction is the set chosen. Scored as one
    yes or no per worker, each worker's `yes` and `no` a label of its own (`score_choice`), so a decider that sends
    everyone, or no one, fails the floor. Diagnostic: how many briefs got exactly the right set."""
    def answers(chosen: Any) -> dict[str, str]:
        return {w: "yes" if w in (chosen or ()) else "no" for w in workers}

    expanded = GoldSet(name=f"{gold.name}-per-worker", version=gold.version, items=[
        GoldItem(id=f"{item.id}:{w}", input="", expected=f"{w}:{answer}")
        for item in gold.items for w, answer in answers(item.expected).items()])
    chosen = {f"{item_id}:{w}": f"{w}:{answer}"
              for item_id, choice in predictions.items() for w, answer in answers(choice).items()}
    result = score_choice(expanded, chosen, floor=floor, name=name)
    exact = sum(sorted(predictions.get(i.id) or []) == sorted(i.expected) for i in gold.items)
    return result.model_copy(update={"diagnostics": {**result.diagnostics, "exact_sets": exact / len(gold.items)}})
