"""T47 (FR-K.8): model-free reachability analyzer for the OKF bundle (the GATE-3a instrument).

Reachability is the compile-side CEILING: could a perfect signpost-reader discover a gold chunk from the
bundle root within the depth and frontier bounds, WITHOUT a model. It separates a scaffolding failure (the
bundle cannot lead to the gold) from a policy failure (the live traversal, T50, does not), so a disappointing
traversal number is diagnosable. It runs before the traversal exists and is the per-corpus reachability spike.

The bundle is two levels (root -> category -> clause). For each (query, gold) the ceiling is optimistic:

  depth-1 (root -> category): the query routes to its category subtree. Reachable here iff the gold's ENRICHED
    category equals the query's category (the `category_tree` channel). Uncategorized gold fails this.
  depth-2 (category -> clause): within the routed subtree of size N, the gold is selectable iff N <= the
    frontier budget (`frontier_cover`: a perfect reader opens them all), OR the gold's `description` carries
    lexical signal for the query, OR its `tags` do.

Two readings are reported separately: connectivity reachability (is the gold present in the bundle at all) and
signpost reachability within the bounds. Each reached gold records the channel that carried each hop, so the
ablation (eval/ablation.py) can attribute the ceiling to channels. Lexical match is a prefix-stem overlap
tightened so a single incidental common word does not count: it fires only on a shared DISTINCTIVE term (a
long/rare token) or on >=2 shared content stems. The ceiling stays an upper bound (a perfect reader could use
any real signal), but a lone "law"/"rights" overlap no longer inflates it.

Run: uv run python -m eval.reachability
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from eval.okf_gold import OkfGold, all_gold, any_gold, build_okf_gold
from rag_wright.okf.document import parse_okf

BUNDLE_ROOT = Path("data/acord/okf/bundle")
DEFAULT_FRONTIER_BUDGET = 50  # clause bodies a traversal may open per routed category (recall@50 spirit)
_UNCATEGORIZED = "_uncategorized"

# Signpost channels a hop can be carried by.
CATEGORY_TREE = "category_tree"
DESCRIPTION = "description"
TAGS = "tags"
FRONTIER_COVER = "frontier_cover"
CROSS_LINKS = "cross_links"  # a gold linked (1 hop, undirected) to a base-reachable clause (T49)
ALL_CHANNELS: frozenset[str] = frozenset({CATEGORY_TREE, DESCRIPTION, TAGS, FRONTIER_COVER, CROSS_LINKS})

_STEM_PREFIX = 5  # a shared 5-char prefix is the model-free "same word" proxy (indemnif-ies/-ication)
_DISTINCTIVE_LEN = 8  # a token this long is rare/discriminating enough that one shared match is signal

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "shall", "any", "are", "was", "its", "all",
    "not", "which", "such", "into", "under", "upon", "each", "other", "party", "parties", "agreement",
    "against", "between", "including", "pursuant", "hereto", "herein", "thereof", "therein", "provided",
    "subject", "respect", "relating", "connection", "otherwise", "without", "within", "applicable",
    "whether", "either", "neither", "have", "has", "will", "may", "must", "during", "after", "before",
    "prior", "following", "above", "below", "hereunder", "hereof", "respective", "including", "regarding",
}


class Signpost(BaseModel):
    """One clause's navigational fields as compiled into the bundle (FR-K.2)."""

    category: str  # enriched category, or "_uncategorized"
    description: str
    tags: list[str]
    categorized: bool


class QueryReach(BaseModel):
    """Per-query reachability over its gold set."""

    query_id: str
    gold_total: int
    gold_present: int  # connectivity: how many gold chunks exist in the bundle
    gold_reachable: int  # signpost-reachable within bounds
    any_gold_reachable: bool
    all_gold_reachable: bool


class ReachabilityReport(BaseModel):
    """The reachability ceiling over the query population, stamped with the compile-recipe version."""

    recipe_version: str
    frontier_budget: int
    channels: list[str]
    n_queries: int
    n_gold_chunks: int
    connectivity_rate: float  # fraction of gold chunks present in the bundle
    any_gold_rate: float  # fraction of queries with >=1 gold signpost-reachable
    all_gold_rate: float  # fraction of queries with ALL gold signpost-reachable
    gold_reachable_rate: float  # fraction of (query, gold) instances reachable -- the recall-relevant ceiling
    channel_histogram: dict[str, int]  # depth-2 carrying channel -> count of gold chunks reached by it
    per_query: dict[str, QueryReach]


def _content_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3 and t not in _STOPWORDS]


def _stem(token: str) -> str:
    return token if len(token) < _STEM_PREFIX else token[:_STEM_PREFIX]


def _lexical_match(query_text: str, signpost_text: str) -> bool:
    """Match on a shared DISTINCTIVE term (a long/rare token) or on >=2 shared content stems.

    Tighter than a bare any-overlap: one incidental common word ("law", "rights") no longer counts, so the
    ceiling reflects real lexical signal rather than coincidence, while a single rare-term hit still fires.
    """
    q = _content_tokens(query_text)
    s = _content_tokens(signpost_text)
    shared = {_stem(t) for t in q} & {_stem(t) for t in s}
    if not shared:
        return False
    distinctive = {_stem(t) for t in (q + s) if len(t) >= _DISTINCTIVE_LEN}
    return bool(shared & distinctive) or len(shared) >= 2


def load_signposts(bundle_root: Path = BUNDLE_ROOT) -> dict[str, Signpost]:
    """Read the compiled bundle's signposts as {chunk_id: Signpost} from each concept file's frontmatter."""
    out: dict[str, Signpost] = {}
    for md in bundle_root.rglob("*.md"):
        if md.name in ("index.md", "log.md"):
            continue
        fm, _ = parse_okf(md.read_text(encoding="utf-8"))
        chunk_id = fm.get("chunk_id")
        if not chunk_id:
            continue
        category = str(fm.get("category") or _UNCATEGORIZED)
        out[str(chunk_id)] = Signpost(
            category=category,
            description=str(fm.get("description") or ""),
            tags=[str(t) for t in (fm.get("tags") or [])],
            categorized=category != _UNCATEGORIZED,
        )
    return out


def category_sizes(signposts: dict[str, Signpost]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for sp in signposts.values():
        sizes[sp.category] = sizes.get(sp.category, 0) + 1
    return sizes


def load_cross_links(bundle_root: Path = BUNDLE_ROOT) -> dict[str, list[str]]:
    """Directed out-adjacency {chunk_id: [neighbour chunk_ids]} from each concept's `## Related` links (T49)."""
    link_re = re.compile(r"\]\(([^)]+)\)")
    path_to_id: dict[str, str] = {}
    related_paths: dict[str, list[str]] = {}
    for md in bundle_root.rglob("*.md"):
        if md.name in ("index.md", "log.md"):
            continue
        fm, body = parse_okf(md.read_text(encoding="utf-8"))
        chunk_id = fm.get("chunk_id")
        if not chunk_id:
            continue
        rel = "/" + str(md.relative_to(bundle_root))
        path_to_id[rel] = str(chunk_id)
        if "## Related clauses" in body:
            section = body.split("## Related clauses", 1)[1]
            related_paths[str(chunk_id)] = [m.group(1).split("#", 1)[0] for m in link_re.finditer(section)]
    return {
        cid: [path_to_id[p] for p in paths if p in path_to_id]
        for cid, paths in related_paths.items()
    }


def _reach_base(
    query_category: str | None,
    query_text: str,
    sp: Signpost,
    total: int,
    sizes: dict[str, int],
    frontier_budget: int,
    channels: frozenset[str],
) -> tuple[bool, list[str]]:
    """Base (link-free) reachability: category routing then frontier-cover / description / tags."""
    path: list[str] = []
    if CATEGORY_TREE in channels:
        if not (sp.categorized and sp.category == query_category):
            return False, []  # the query routes to a different subtree; the gold is not under it
        pool = sizes.get(sp.category, 0)
        path.append(CATEGORY_TREE)
    else:
        pool = total  # category channel ablated -> no narrowing; the pool is the whole corpus

    if FRONTIER_COVER in channels and pool <= frontier_budget:
        return True, path + [FRONTIER_COVER]
    if DESCRIPTION in channels and _lexical_match(query_text, sp.description):
        return True, path + [DESCRIPTION]
    if TAGS in channels and _lexical_match(query_text, " ".join(sp.tags)):
        return True, path + [TAGS]
    return False, path


def reach_chunk(
    query_category: str | None,
    query_text: str,
    gold_chunk_id: str,
    signposts: dict[str, Signpost],
    *,
    frontier_budget: int,
    channels: frozenset[str],
    sizes: dict[str, int] | None = None,
    cross_links: dict[str, list[str]] | None = None,
) -> tuple[bool, list[str]]:
    """Is `gold_chunk_id` signpost-reachable for the query within bounds? Returns (reachable, hop channels).

    If the base channels do not reach it and CROSS_LINKS is active, the gold is reachable when it is linked
    (1 hop, via the passed undirected adjacency) to a clause that IS base-reachable for the query.
    """
    sp = signposts.get(gold_chunk_id)
    if sp is None:
        return False, []  # not present in the bundle (connectivity failure)
    sizes = sizes if sizes is not None else category_sizes(signposts)
    total = len(signposts)

    reached, path = _reach_base(query_category, query_text, sp, total, sizes, frontier_budget, channels)
    if reached:
        return True, path
    if CROSS_LINKS in channels and cross_links:
        base = channels - {CROSS_LINKS}
        for neighbour in cross_links.get(gold_chunk_id, []):
            n_sp = signposts.get(neighbour)
            if n_sp is None:
                continue
            n_ok, _ = _reach_base(query_category, query_text, n_sp, total, sizes, frontier_budget, base)
            if n_ok:
                return True, [CROSS_LINKS]
    return False, path


def reach_chunk_whatif(
    query_category: str | None,
    query_text: str,
    gold_chunk_id: str,
    signposts: dict[str, Signpost],
    *,
    override: Signpost,
    frontier_budget: int,
    channels: frozenset[str],
) -> tuple[bool, list[str]]:
    """Recompute one gold chunk's reachability under a hypothetical signpost, without recompiling the bundle."""
    patched = dict(signposts)
    patched[gold_chunk_id] = override
    return reach_chunk(query_category, query_text, gold_chunk_id, patched,
                       frontier_budget=frontier_budget, channels=channels)


def _recipe_version(bundle_root: Path) -> str:
    index = bundle_root / "index.md"
    if not index.exists():
        return "unknown"
    fm, _ = parse_okf(index.read_text(encoding="utf-8"))
    return str(fm.get("compile_recipe_version") or "unknown")


def _undirected(cross_links: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
    """Union out-links and in-links, so a gold is 'linked' to any clause that lists it or that it lists."""
    if not cross_links:
        return cross_links
    adj: dict[str, set[str]] = {k: set(v) for k, v in cross_links.items()}
    for src, nbrs in cross_links.items():
        for nbr in nbrs:
            adj.setdefault(nbr, set()).add(src)
    return {k: sorted(v) for k, v in adj.items()}


def compute_reachability(
    gold: OkfGold,
    signposts: dict[str, Signpost],
    *,
    frontier_budget: int = DEFAULT_FRONTIER_BUDGET,
    channels: frozenset[str] = ALL_CHANNELS,
    cross_links: dict[str, list[str]] | None = None,
    bundle_root: Path = BUNDLE_ROOT,
) -> ReachabilityReport:
    """Compute the reachability ceiling over the query population (cross_links optional, T49)."""
    sizes = category_sizes(signposts)
    adjacency = _undirected(cross_links)
    channel_histogram: dict[str, int] = {}
    per_query: dict[str, QueryReach] = {}
    gold_present_total = gold_total = 0

    for qid, q in gold.queries.items():
        reachable: list[str] = []
        for cid in q.gold_chunk_ids:
            gold_total += 1
            if cid in signposts:
                gold_present_total += 1
            ok, path = reach_chunk(q.category, q.text, cid, signposts, frontier_budget=frontier_budget,
                                   channels=channels, sizes=sizes, cross_links=adjacency)
            if ok:
                reachable.append(cid)
                channel_histogram[path[-1]] = channel_histogram.get(path[-1], 0) + 1
        per_query[qid] = QueryReach(
            query_id=qid,
            gold_total=len(q.gold_chunk_ids),
            gold_present=sum(1 for c in q.gold_chunk_ids if c in signposts),
            gold_reachable=len(reachable),
            any_gold_reachable=any_gold(q.gold_chunk_ids, reachable),
            all_gold_reachable=all_gold(q.gold_chunk_ids, reachable),
        )

    n = len(per_query) or 1
    reachable_instances = sum(r.gold_reachable for r in per_query.values())
    return ReachabilityReport(
        recipe_version=_recipe_version(bundle_root),
        frontier_budget=frontier_budget,
        channels=sorted(channels),
        n_queries=len(per_query),
        n_gold_chunks=gold_total,
        connectivity_rate=(gold_present_total / gold_total) if gold_total else 1.0,
        any_gold_rate=sum(1 for r in per_query.values() if r.any_gold_reachable) / n,
        all_gold_rate=sum(1 for r in per_query.values() if r.all_gold_reachable) / n,
        gold_reachable_rate=(reachable_instances / gold_total) if gold_total else 0.0,
        channel_histogram=dict(sorted(channel_histogram.items())),
        per_query=per_query,
    )


def main() -> None:
    gold = build_okf_gold()
    signposts = load_signposts()
    cross_links = load_cross_links()
    n_links = sum(len(v) for v in cross_links.values())
    print(f"[reachability] {len(signposts)} bundle signposts, {gold.distinct_gold_chunks} distinct gold chunks, "
          f"{n_links} cross-links over {len(cross_links)} clauses")
    for budget in (20, 50, 100):
        base = compute_reachability(gold, signposts, frontier_budget=budget, cross_links=None)
        linked = compute_reachability(gold, signposts, frontier_budget=budget, cross_links=cross_links)
        print(f"\n[reachability] recipe={base.recipe_version} frontier_budget={budget}")
        print(f"  no-links : any-gold {base.any_gold_rate:.3f} | all-gold {base.all_gold_rate:.3f} | "
              f"per-gold recall ceiling {base.gold_reachable_rate:.3f}")
        print(f"  + links  : any-gold {linked.any_gold_rate:.3f} | all-gold {linked.all_gold_rate:.3f} | "
              f"per-gold recall ceiling {linked.gold_reachable_rate:.3f}  "
              f"(delta {linked.gold_reachable_rate - base.gold_reachable_rate:+.3f})")
        print(f"  + links channel histogram: {linked.channel_histogram}")


if __name__ == "__main__":
    main()
