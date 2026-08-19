"""Issue 0005 route-(b) QUALITY A/B (RuleWright's ask -- they can't measure it product-side: ADR-0054 removed
auto-tag from evidence, ADR-0047 retired the function label as a retrieval pre-filter). Guided decoding
HARD-CONSTRAINS the function label to the closed enum; free-text tag-parse does NOT (the model can emit any
string, cleaned downstream) -- so route (b) could shift classification quality. Measure it against CUAD ground
truth (the LegalBERT holdout: each span carries its gold clause function or NONE).

Runs BOTH classifiers on the SAME spans and reports, for each: top-1 accuracy on TYPED spans (does the primary
function match gold) and the no-function rate on NONE spans (false positives), plus inter-path agreement. Both
call the model on the ASYNC deadline-bounded path, so a guided runaway is a cancelled -> empty classification
(counted as a miss -- its real failure mode), never a hung eval. Streams `[ab] i/N` progress (monitor it).

  uv run python scripts/eval_classifier_guided_vs_tagparse.py --per-label 4 --none 120
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from pathlib import Path

_NONE = "NONE"


# --- pure metrics (hermetically tested) -----------------------------------------------------------------------

def top1(scores: list) -> str:
    """The primary (rank-0) function label a classifier assigned, or NONE if it assigned none."""
    return scores[0].function if scores else _NONE


def score_rows(rows: list[dict]) -> dict:
    """rows: [{gold, guided_top1, tag_top1}]. -> top-1 accuracy on TYPED spans (path_top1 == gold), no-function
    rate on NONE spans (path_top1 == NONE), and inter-path agreement, for each path."""
    typed = [r for r in rows if r["gold"] != _NONE]
    none = [r for r in rows if r["gold"] == _NONE]

    def _rate(rs: list[dict], key: str, target: str | None) -> float | None:
        # target=None -> compare against each row's own gold (typed accuracy); else a fixed target (NONE rate)
        if not rs:
            return None
        hits = sum(1 for r in rs if r[key] == (r["gold"] if target is None else target))
        return round(hits / len(rs), 3)

    return {
        "typed_n": len(typed), "none_n": len(none),
        "guided_typed_top1": _rate(typed, "guided_top1", None),
        "tag_typed_top1": _rate(typed, "tag_top1", None),
        "guided_none_nofunc": _rate(none, "guided_top1", _NONE),
        "tag_none_nofunc": _rate(none, "tag_top1", _NONE),
        "agreement": round(sum(1 for r in rows if r["guided_top1"] == r["tag_top1"]) / len(rows), 3) if rows else None,
    }


# --- the A/B run (needs a model) ------------------------------------------------------------------------------

def _log(m: str) -> None:
    print(m, flush=True)


def _stratified_sample(texts: list, gold: list, per_label: int, none_n: int) -> list[tuple[str, str]]:
    """Deterministic representative sample: up to `per_label` spans of EACH typed function (cover the label
    space) + `none_n` NONE spans (measure false positives). Deterministic (sorted by index, strided)."""
    by_label: dict[str, list[str]] = defaultdict(list)
    nones: list[str] = []
    for t, g in zip(texts, gold):
        (nones if g == _NONE else by_label[g]).append((t, g))
    out: list[tuple[str, str]] = []
    for _label, items in sorted(by_label.items()):
        out.extend(items[:per_label])
    stride = max(1, len(nones) // none_n) if none_n else 1
    out.extend(nones[::stride][:none_n])
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", default="data/cache/legalbert_holdout_eval.json")
    ap.add_argument("--per-label", type=int, default=4, help="typed spans per function label")
    ap.add_argument("--none", type=int, default=120, help="NONE spans (false-positive probe)")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--model", default=None,
                    help="classifier model id (default: the GENERAL role). e.g. google/gemma-4-31b-it -- "
                         "tag-parse runs on ANY model, so this is a per-stage override.")
    ap.add_argument("--guided-model", default=None,
                    help="optional reference-ceiling model for the GUIDED arm (a guided-capable model, e.g. "
                         "deepseek/deepseek-v4-pro). Default: same as --model.")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv()
    from rag_wright.contracts.function import FUNCTION_LABELS
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.models.seam import build_structured
    from rag_wright.spans.clause_function_classifier import (
        _CLAUSE_TAG_INSTRUCTIONS,
        _PROMPT,
        ClauseFunctionClassification,
        _TagClassifierRunnable,
        _to_scores,
        parse_clause_function_tags,
    )

    d = json.loads(Path(args.holdout).read_text())
    sample = _stratified_sample(d["texts"], d["gold"], args.per_label, args.none)
    n = len(sample)
    tag_model = args.model or model_for(ModelRole.GENERAL)   # per-stage override: tag-parse runs on any model
    guided_model = args.guided_model or tag_model
    _log(f"[ab] {n} spans ({sum(1 for _, g in sample if g != _NONE)} typed / "
         f"{sum(1 for _, g in sample if g == _NONE)} NONE) | tag-parse model={tag_model} | "
         f"guided model={guided_model}")

    guided = build_structured(guided_model, ClauseFunctionClassification, label="ab.guided")
    tagr = _TagClassifierRunnable(tag_model, instructions=_CLAUSE_TAG_INSTRUCTIONS,
                                  parse=parse_clause_function_tags, label="ab.tagparse")
    labels_block = "\n".join(FUNCTION_LABELS)
    sem = asyncio.Semaphore(args.concurrency)
    fails = Counter()

    async def _classify(runnable, text: str, which: str) -> str:
        prompt = _PROMPT.format(labels=labels_block, text=text)
        try:
            raw = await runnable.ainvoke(prompt)
        except Exception as exc:  # noqa: BLE001 - a runaway/parse failure -> empty (its real failure mode)
            fails[f"{which}:{type(exc).__name__}"] += 1
            return _NONE
        scores = _to_scores(raw.functions) if isinstance(raw, ClauseFunctionClassification) else []
        return top1(scores)

    done = {"i": 0}

    async def _one(idx: int, text: str, g: str) -> dict:
        async with sem:
            gt = await _classify(guided, text, "guided")
            tt = await _classify(tagr, text, "tag")
        done["i"] += 1
        if done["i"] % 10 == 0 or done["i"] == n:
            _log(f"[ab] {done['i']}/{n}")
        return {"gold": g, "guided_top1": gt, "tag_top1": tt}

    rows = await asyncio.gather(*(_one(i, t, g) for i, (t, g) in enumerate(sample)))

    r = score_rows(rows)
    _log("\n[ab] === CLASSIFICATION QUALITY: guided (build_structured) vs tag-parse (route b) ===")
    _log(f"[ab] typed spans (n={r['typed_n']}): top-1 vs gold  guided={r['guided_typed_top1']}  "
         f"tag-parse={r['tag_typed_top1']}")
    _log(f"[ab] NONE spans  (n={r['none_n']}): no-function rate  guided={r['guided_none_nofunc']}  "
         f"tag-parse={r['tag_none_nofunc']}")
    _log(f"[ab] inter-path agreement (same top-1): {r['agreement']}")
    if fails:
        _log(f"[ab] model failures: {dict(fails)}")


if __name__ == "__main__":
    asyncio.run(main())
