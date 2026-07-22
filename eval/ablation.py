"""T47 (FR-K.8): signpost-channel ablation over the reachability ceiling.

Recompute reachability with one signpost channel removed at a time, so a disappointing (or a strong) ceiling
is attributable to a channel rather than a single opaque number. The reachability cost of a channel is the
drop in the any-gold rate between the full run and the run with that channel removed. This is what turns a
GATE-3a redirect into a targeted fix ("descriptions carry the ceiling; category tree adds X") instead of a
guess. Evaluation software: model-free, deterministic, registers nothing.

Run: uv run python -m eval.ablation
"""

from __future__ import annotations

from eval.okf_gold import OkfGold, build_okf_gold
from eval.reachability import (
    ALL_CHANNELS,
    CATEGORY_TREE,
    DEFAULT_FRONTIER_BUDGET,
    DESCRIPTION,
    FRONTIER_COVER,
    ReachabilityReport,
    Signpost,
    TAGS,
    compute_reachability,
    load_signposts,
)

_ABLATABLE = (CATEGORY_TREE, DESCRIPTION, TAGS, FRONTIER_COVER)


def run_ablation(
    gold: OkfGold, signposts: dict[str, Signpost], *, frontier_budget: int = DEFAULT_FRONTIER_BUDGET
) -> dict[str, ReachabilityReport]:
    """Full reachability plus one run per removed channel, keyed 'full' / 'minus_<channel>'."""
    results: dict[str, ReachabilityReport] = {
        "full": compute_reachability(gold, signposts, frontier_budget=frontier_budget, channels=ALL_CHANNELS)
    }
    for channel in _ABLATABLE:
        results[f"minus_{channel}"] = compute_reachability(
            gold, signposts, frontier_budget=frontier_budget, channels=ALL_CHANNELS - {channel}
        )
    return results


def main() -> None:
    gold = build_okf_gold()
    signposts = load_signposts()
    results = run_ablation(gold, signposts)
    full = results["full"].any_gold_rate
    print(f"[ablation] frontier_budget={DEFAULT_FRONTIER_BUDGET}  full any-gold rate: {full:.3f}\n")
    print(f"  {'channel removed':<20} {'any-gold':>9} {'cost':>8}")
    for channel in _ABLATABLE:
        rate = results[f"minus_{channel}"].any_gold_rate
        print(f"  {channel:<20} {rate:>9.3f} {full - rate:>8.3f}")


if __name__ == "__main__":
    main()
