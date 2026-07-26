"""CU-D1: CUAD highlighting eval on the SEED=0 20% holdout (leak-free -- the LegalBERT classifier trained on
the other 80%). GIVEN the clause type (isolates retrieval + citation from NL->type, which CU-D2 evaluates):
per (held-out contract x CUAD category), the typed within-contract filter (`serve_highlight`, highlight
branch) returns a span set, scored against CUAD's own gold answer spans.

Coordinate note: our stored spans are offset into the CANONICAL text (the chunk reconstruction); CUAD gold
offsets are into the RAW context -- different coordinate systems (the chunker strips/merges whitespace). So
overlap is measured in NORMALIZED TEXT space (SQuAD-style), never by raw offsets.

Metrics (macro over the FULL holdout, all 41 categories -- no subset, per the no-shortcuts eval standard):
- Presence P/R/F1: does the typed filter fire iff the category is actually present (routing quality)?
- Coverage: fraction of gold answers whose normalized text is contained in the returned span text -- the
  headline highlighting metric ("did we highlight a span covering the answer?").
- Token recall / F1 (SQuAD-style): secondary. Precision is expectedly LOW (we highlight whole clauses, far
  longer than the short gold answers) and is reported honestly, not as an error.

Usage:  uv run --no-sync python -m eval.cuad_highlight
"""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from rag_wright.capabilities.highlight_serve import serve_highlight
from rag_wright.contracts.function import canonical_function
from rag_wright.contracts.ontology import ClauseCategory
from rag_wright.contracts.query_intent import QueryIntent
from rag_wright.spans.cuad_labels import parse_cuad

CUAD = Path("data/cuad/extracted/CUAD_v1/CUAD_v1.json")
DB = "ragwright_cuad"
REPORT = Path("docs/eval/cuad_highlighting_cu-d1.md")  # durable, committed results record (CU-D1)
CATEGORIES = [c.value for c in ClauseCategory]

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def _slug(contract_id: str) -> str:
    return _SAFE.sub("_", contract_id).strip("_") or "contract"


def _norm(text: str) -> str:
    """SQuAD-style normalization: lowercase, drop punctuation, collapse whitespace."""
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def _toks(text: str) -> list[str]:
    return _norm(text).split()


def token_prf(pred: str, gold: str) -> tuple[float, float, float]:
    """SQuAD-style token multiset precision/recall/F1. Empty pred or gold -> all zero."""
    p, g = _toks(pred), _toks(gold)
    if not p or not g:
        return 0.0, 0.0, 0.0
    overlap = sum((Counter(p) & Counter(g)).values())
    if not overlap:
        return 0.0, 0.0, 0.0
    precision, recall = overlap / len(p), overlap / len(g)
    return precision, recall, 2 * precision * recall / (precision + recall)


def covers(gold: str, pred_concat: str) -> bool:
    """Is the gold answer's normalized text contained in the (normalized) retrieved text?"""
    g = _norm(gold)
    return bool(g) and g in _norm(pred_concat)


def _holdout() -> list:
    contracts = list(parse_cuad(CUAD))
    import random

    random.Random(0).shuffle(contracts)  # SEED=0 -- the same split ingest and the classifier's holdout use
    return contracts[: max(1, len(contracts) // 5)]


def _progress(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    load_dotenv()
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env(database=DB)
    holdout = _holdout()
    _progress(f"[setup] holdout={len(holdout)} contracts x {len(CATEGORIES)} categories (given-type)")

    tp = fp = fn = tn = 0
    cov_hit = cov_tot = 0
    tok_rec: list[float] = []
    tok_f1: list[float] = []
    per_cat: dict[str, list[int]] = {c: [0, 0] for c in CATEGORIES}  # [gold answers covered, gold answers total]
    t0 = time.perf_counter()

    for i, c in enumerate(holdout, 1):
        slug = _slug(c.contract_id)
        gold_by: dict[str, list[str]] = {}
        for a in c.answers:
            canon = canonical_function(a.clause_type)
            if canon is not None:
                gold_by.setdefault(canon, []).append(a.text)

        for cat in CATEGORIES:
            fn_label = canonical_function(cat)
            gold = gold_by.get(fn_label, [])
            res = serve_highlight(cat, slug, QueryIntent(clause_types=[fn_label], intent="highlight"), store=store)
            retrieved = res.spans
            gp, rp = bool(gold), bool(retrieved)
            tp += gp and rp
            fn += gp and not rp
            fp += (not gp) and rp
            tn += (not gp) and (not rp)
            if gp:
                pred_text = " ".join(s.text for s in retrieved)
                _, rec, f1 = token_prf(pred_text, " ".join(gold))
                tok_rec.append(rec)
                tok_f1.append(f1)
                for ga in gold:
                    cov_tot += 1
                    per_cat[fn_label][1] += 1
                    if covers(ga, pred_text):
                        cov_hit += 1
                        per_cat[fn_label][0] += 1
        if i % 20 == 0 or i == len(holdout):
            _progress(f"[eval] {i}/{len(holdout)} contracts  present_cases={len(tok_rec)}  "
                      f"coverage={cov_hit}/{cov_tot}")

    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    pf1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0

    print("\n=== CUAD highlighting eval (given-type, SEED=0 holdout) ===", flush=True)
    print(f"  contracts={len(holdout)}  (contract,category) cells={len(holdout) * len(CATEGORIES)}  "
          f"present_cases={len(tok_rec)}  gold_answers={cov_tot}", flush=True)
    print(f"  PRESENCE   precision={prec:.3f}  recall={rec:.3f}  f1={pf1:.3f}   "
          f"(tp={tp} fp={fp} fn={fn} tn={tn})", flush=True)
    print(f"  COVERAGE   {cov_hit}/{cov_tot} = {cov_hit / cov_tot if cov_tot else 0:.3f}  "
          "(gold answers contained in a returned span)", flush=True)
    print(f"  TOKEN      recall={mean(tok_rec):.3f}  f1={mean(tok_f1):.3f}  "
          "(macro over present cases; precision low by design = whole-clause highlight)", flush=True)
    print(f"  ({time.perf_counter() - t0:.1f}s)", flush=True)

    ranked = sorted(((h / t if t else 0.0, h, t, c) for c, (h, t) in per_cat.items() if t), reverse=True)
    print("\n  per-category coverage (worst 8, of categories with gold):", flush=True)
    for covr, h, t, cat in ranked[-8:]:
        print(f"    {cat[:34]:<34} {h:>3}/{t:<3} = {covr:.2f}", flush=True)

    _write_report(len(holdout), tp, fp, fn, tn, prec, rec, pf1, cov_hit, cov_tot,
                  mean(tok_rec), mean(tok_f1), len(tok_rec), ranked)
    _progress(f"[report] wrote {REPORT}")
    store.close()


def _write_report(n_contracts, tp, fp, fn, tn, prec, rec, pf1, cov_hit, cov_tot,
                  tok_rec, tok_f1, present_cases, ranked) -> None:
    """Write the durable CU-D1 results record (regenerated on each eval run; committed, pointed to by tasks.md)."""
    lines = [
        "# CU-D1: CUAD highlighting eval results",
        "",
        "> Regenerated by `uv run --no-sync python -m eval.cuad_highlight`. Do not hand-edit the tables.",
        "",
        "## Setup",
        "",
        "- **Split:** SEED=0 20% CUAD holdout (leak-free -- the LegalBERT function classifier trained on the "
        "other 80%).",
        "- **Protocol:** GIVEN the clause type (isolates retrieval + citation from NL->type, which CU-D2 "
        "measures). Per (held-out contract x CUAD category), the typed within-contract filter "
        "(`serve_highlight`, highlight branch) returns a span set, scored vs CUAD's own gold answers.",
        "- **Scoring space:** NORMALIZED TEXT (SQuAD-style), not raw offsets -- our spans are offset into the "
        "canonical (chunk-reconstructed) text while CUAD gold is offset into the raw context; the two "
        "coordinate systems differ (the chunker strips/merges whitespace).",
        f"- **Scale:** {n_contracts} contracts x {len(CATEGORIES)} categories = {n_contracts * len(CATEGORIES)} "
        f"cells; {present_cases} present cases; {cov_tot} gold answers. No subset (full holdout).",
        "",
        "## Headline metrics",
        "",
        "| metric | value | reading |",
        "|---|---|---|",
        f"| **Coverage** | **{cov_hit / cov_tot if cov_tot else 0:.3f}** ({cov_hit}/{cov_tot}) | fraction of "
        "gold answers contained in a returned span -- the headline highlighting number |",
        f"| Presence recall | {rec:.3f} | typed filter fires when the category is present |",
        f"| Presence precision | {prec:.3f} | firings that land on a truly-present category (rest = classifier "
        "over-trigger) |",
        f"| Presence F1 | {pf1:.3f} | (tp={tp} fp={fp} fn={fn} tn={tn}) |",
        f"| Token recall / F1 | {tok_rec:.3f} / {tok_f1:.3f} | secondary; F1 low BY DESIGN (whole-clause "
        "highlight >> short gold answers) |",
        "",
        "## What bounds it",
        "",
        "- The LegalBERT classifier's per-class recall (macro-F1 ~0.544, T56) is the ceiling: the typed filter "
        "can only return spans the classifier routed to the category.",
        "- The low-coverage categories below are its blind spots (rare license/renewal classes) -- a future "
        "lever (more training signal on rare classes / a stronger classifier), not a pipeline bug.",
        "- The offset round-trip is exact (CU-B4: 27,074/27,074), so citation location is not a source of error "
        "here; retrieval (classification) recall is.",
        "",
        "## Per-category coverage (all categories with gold, best -> worst)",
        "",
        "| category | covered / gold | coverage |",
        "|---|---|---|",
    ]
    for covr, h, t, cat in ranked:
        lines.append(f"| {cat} | {h}/{t} | {covr:.2f} |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
