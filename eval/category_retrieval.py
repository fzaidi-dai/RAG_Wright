"""T48 (FR-C.3): the category-label retrieval control arm for GATE-3.

The cheapest mechanism that bypasses the diagnosed query-representation gap: retrieve the clauses whose
manufactured category (T46) equals the query's category, instead of ranking by an embedding. Run as the
CONTROL for GATE-3, not an independent experiment -- it establishes how much of the available lift comes from
coarse label matching alone, so OKF traversal (T50) is measured against a fair alternative rather than against
the two-leg 0.379 baseline it will trivially differ from. Under the post-GATE-3a framing (ADR-0022 addendum 2)
this is a COST/VALUE control, not a viability kill-switch: if coarse labels already capture the lift, OKF
traversal's complexity is not justified.

Category-label retrieval has no intra-category ranking (a label is binary), so within a matched category the
bucket is returned in a deterministic id order. Recall@k therefore reflects bucket membership plus position;
`containment` (the fraction of gold in the query's bucket, ranking-independent) is reported as the arm's ceiling.
Cost: no model call, negligible latency (a dict lookup) -- the cheapest arm on the T50 cost axes.

Run: uv run python -m eval.category_retrieval
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from eval.acord import ACORD_DIR, AcordQuery, load_test_queries
from eval.acord_retrieval import AcordRetrievalReport, Retrieve, evaluate_retrieval
from eval.okf_gold import any_gold  # noqa: F401  (kept for parity with the reachability readings)
from eval.reachability import BUNDLE_ROOT, load_signposts

_UNCATEGORIZED = "_uncategorized"

# Recorded reference points (not recomputed here): the two-leg baseline and the T47 reachability ceiling.
TWO_LEG_RECALL_AT_50 = 0.379
REACHABILITY_RECALL_CEILING = 0.776


def members_by_category(corpus_categories: dict[str, str]) -> dict[str, list[str]]:
    """Invert corpus-id -> category into category -> sorted [corpus-id], excluding the uncategorized bucket."""
    members: dict[str, list[str]] = defaultdict(list)
    for corpus_id, category in sorted(corpus_categories.items()):
        if category != _UNCATEGORIZED:
            members[category].append(corpus_id)
    return dict(members)


def make_category_retrieve(text_to_category: dict[str, str], members: dict[str, list[str]]) -> Retrieve:
    """A `retrieve(query_text) -> ranked corpus-ids`: the query's category bucket (deterministic id order)."""

    def retrieve(query_text: str) -> list[str]:
        category = text_to_category.get(query_text)
        if category is None:
            return []
        return list(members.get(category, []))

    return retrieve


def containment(
    queries: list[AcordQuery], text_to_category: dict[str, str], members: dict[str, list[str]]
) -> float:
    """Mean fraction of a query's gold clauses that fall in its category bucket (the arm's recall ceiling)."""
    if not queries:
        return 0.0
    total = 0.0
    for q in queries:
        bucket = set(members.get(text_to_category.get(q.text, ""), []))
        total += (len(q.relevant & bucket) / len(q.relevant)) if q.relevant else 0.0
    return total / len(queries)


def evaluate_category_control(
    queries: list[AcordQuery], text_to_category: dict[str, str], corpus_categories: dict[str, str]
) -> tuple[AcordRetrievalReport, float]:
    """Score the control arm: (recall@50/10 + nDCG@10 via the shared harness, the arm's containment ceiling)."""
    members = members_by_category(corpus_categories)
    report = evaluate_retrieval(queries, make_category_retrieve(text_to_category, members))
    return report, containment(queries, text_to_category, members)


def corpus_categories_from_bundle(bundle_root: Path = BUNDLE_ROOT) -> dict[str, str]:
    """corpus-id -> category, read from the compiled bundle signposts (T46). corpus-id = source_doc_id."""
    return {cid.rsplit(":", 2)[0]: sp.category for cid, sp in load_signposts(bundle_root).items()}


def query_categories(acord_dir: Path = ACORD_DIR) -> dict[str, str]:
    """query-text -> attorney category (metadata.category). ACORD query `_id` == `text`."""
    out: dict[str, str] = {}
    for line in (acord_dir / "queries.jsonl").open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        meta = row.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        if isinstance(meta, dict) and meta.get("category"):
            out[str(row["text"])] = str(meta["category"])
    return out


def main() -> None:
    queries = load_test_queries()
    report, contain = evaluate_category_control(queries, query_categories(), corpus_categories_from_bundle())
    print(f"[category_control] {report.n_queries} test queries | cost: 0 model calls, dict-lookup latency\n")
    print(f"  recall@50 : {report.recall_at_50:.3f}   (control arm, category bucket, no intra-category ranking)")
    print(f"  recall@10 : {report.recall_at_10:.3f}")
    print(f"  nDCG@10   : {report.ndcg_at_10:.3f}")
    print(f"  containment (arm ceiling, gold-in-bucket): {contain:.3f}")
    print("\n  reference points:")
    print(f"    two-leg embedding baseline recall@50 : {TWO_LEG_RECALL_AT_50:.3f}")
    print(f"    OKF reachability recall ceiling      : {REACHABILITY_RECALL_CEILING:.3f}  (T47, model-free)")


if __name__ == "__main__":
    main()
