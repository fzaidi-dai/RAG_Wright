"""T47 (FR-K.8, RAC-47): tests for the model-free reachability analyzer + signpost ablation.

Hermetic: signposts and gold are built in memory (no bundle files, no model). The reachability model is an
optimistic ceiling -- "could a perfect signpost-reader reach the gold within the bounds" -- so the tests pin
the channel logic (category routing, frontier-cover, description/tags signal), the any/all-gold readings, the
ablation drops, the signpost what-if, and connectivity.
"""

from __future__ import annotations

from eval.ablation import run_ablation
from eval.okf_gold import OkfGold, QueryGold
from eval.reachability import (
    ALL_CHANNELS,
    CATEGORY_TREE,
    DESCRIPTION,
    FRONTIER_COVER,
    TAGS,
    Signpost,
    compute_reachability,
    reach_chunk,
    reach_chunk_whatif,
)


def _signposts() -> dict[str, Signpost]:
    sp = {
        "g1": Signpost(category="Governing Law", description="New York law governs the agreement.",
                       tags=["Governing Law"], categorized=True),
        "g2": Signpost(category="Indemnification", description="Supplier indemnifies buyer against claims.",
                       tags=["Indemnification"], categorized=True),
        "g3": Signpost(category="_uncategorized", description="Miscellaneous boilerplate provision.",
                       tags=[], categorized=False),
    }
    # pad Indemnification past a small frontier budget so g2 must be reached by signal, not frontier-cover
    for i in range(60):
        sp[f"ind{i}"] = Signpost(category="Indemnification", description="Other indemnity wording.",
                                 tags=["Indemnification"], categorized=True)
    return sp


def _gold() -> OkfGold:
    def q(qid, text, category, golds):
        return QueryGold(query_id=qid, text=text, category=category, split="held_out",
                         gold_chunk_ids=golds, gold_corpus_ids=golds)
    return OkfGold(
        relevance_floor=2, debug_fraction=0.2,
        queries={
            "q_gov": q("q_gov", "New York Governing Law", "Governing Law", ["g1"]),
            "q_ind": q("q_ind", "indemnification obligations", "Indemnification", ["g2", "g3"]),
        },
        unmapped_corpus_ids=[], induced_category_labels={},
    )


def test_reach_via_category_and_frontier_cover():
    sp = _signposts()
    ok, path = reach_chunk("Governing Law", "New York Governing Law", "g1", sp,
                           frontier_budget=50, channels=ALL_CHANNELS)
    assert ok is True
    assert path[0] == CATEGORY_TREE  # depth-1 routed by category
    assert FRONTIER_COVER in path  # Governing Law subtree is tiny -> frontier covers it


def test_category_mismatch_is_unreachable():
    sp = _signposts()
    ok, _ = reach_chunk("Governing Law", "New York Governing Law", "g2", sp,
                        frontier_budget=50, channels=ALL_CHANNELS)
    assert ok is False  # g2 is Indemnification; the Governing Law query never routes to it


def test_uncategorized_gold_fails_category_routing():
    sp = _signposts()
    ok, _ = reach_chunk("Indemnification", "indemnification obligations", "g3", sp,
                        frontier_budget=50, channels=ALL_CHANNELS)
    assert ok is False  # g3 is _uncategorized -> the category signpost cannot route to it


def test_reach_by_description_signal_when_pool_exceeds_budget():
    sp = _signposts()
    ok, path = reach_chunk("Indemnification", "indemnification obligations", "g2", sp,
                           frontier_budget=50, channels=ALL_CHANNELS)  # pool ~61 > 50
    assert ok is True
    assert path[-1] == DESCRIPTION  # not frontier-cover; the description carries it


def test_any_and_all_gold_readings():
    report = compute_reachability(_gold(), _signposts(), frontier_budget=50)
    q = report.per_query["q_ind"]
    assert q.any_gold_reachable is True  # g2 reachable
    assert q.all_gold_reachable is False  # g3 unreachable
    assert q.gold_reachable == 1 and q.gold_total == 2
    assert report.any_gold_rate == 1.0  # both queries reach at least one gold
    assert report.all_gold_rate == 0.5  # only q_gov reaches all of its gold


def test_connectivity_reports_missing_gold():
    gold = _gold()
    gold.queries["q_ind"].gold_chunk_ids.append("ghost")  # a gold id absent from the bundle
    report = compute_reachability(gold, _signposts(), frontier_budget=50)
    assert report.connectivity_rate < 1.0  # the ghost is not present


def test_ablation_attributes_the_ceiling_to_channels():
    gold, sp = _gold(), _signposts()
    result = run_ablation(gold, sp, frontier_budget=50)
    full = result["full"].any_gold_rate
    # removing category_tree collapses routing -> reachability must not exceed the full ceiling
    assert result["minus_category_tree"].any_gold_rate <= full
    # g2 sits in a pool larger than the budget, so a lexical channel carries it (not frontier-cover).
    # description and tags are redundant here (tags == [category], query names the category), so removing
    # BOTH is what makes g2 unreachable -- an attributable, honest finding about channel overlap.
    no_lexical = compute_reachability(gold, sp, frontier_budget=50, channels=ALL_CHANNELS - {DESCRIPTION, TAGS})
    assert no_lexical.per_query["q_ind"].any_gold_reachable is False


def test_signpost_whatif_recomputes_without_recompiling():
    sp = _signposts()
    before, _ = reach_chunk("Indemnification", "indemnification obligations", "g3", sp,
                            frontier_budget=50, channels=ALL_CHANNELS)
    assert before is False
    # hypothetically categorize g3 into Indemnification -> now routable, no recompile
    fixed = Signpost(category="Indemnification", description="Miscellaneous boilerplate provision.",
                     tags=["Indemnification"], categorized=True)
    after, path = reach_chunk_whatif("Indemnification", "indemnification obligations", "g3", sp,
                                     override=fixed, frontier_budget=50, channels=ALL_CHANNELS)
    assert after is True
    assert path[0] == CATEGORY_TREE


def test_reachability_is_deterministic():
    a = compute_reachability(_gold(), _signposts(), frontier_budget=50)
    b = compute_reachability(_gold(), _signposts(), frontier_budget=50)
    assert a.model_dump() == b.model_dump()
