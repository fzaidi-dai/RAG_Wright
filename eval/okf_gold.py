"""T45 (FR-K.8, §12): ACORD gold-chunk labels for OKF reachability scoring.

Maps each ACORD test-split query's graded qrels (grade >= floor) onto the ingested `chunk_id`s, so the
reachability analyzer (T47) and the traversal (T50) score against the same gold the T33 recall@50 baseline
uses. ACORD is pre-segmented (a clause is a chunk) and `chunk_id` embeds the ACORD `_id` as `source_doc_id`
at index 0, so the corpus-id -> chunk_id map is exact, not fuzzy: `ChunkId.of(corpus_id, 0, text)` recomputes
what the ingest wrote, and the sidecar (T40) is the authority for what was actually ingested. A gold corpus-id
that is not in the corpus, or whose chunk is not in the sidecar, is reported (never silently dropped).

Also emits the qrels-INDUCED silver category labels: a gold clause inherits its relevant query's
`metadata.category` (the loader ignores that field), the label source for the T46 category signpost and the
T48 control. And it declares a deterministic, stratified-by-category debug split held out of the GATE-3
headline, so a recipe tuned on the debug queries (T51) is scored on queries it was not tuned against.

Rebuild: uv run python -m eval.okf_gold
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from math import floor
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.store.chunk_text import ChunkTextStore

from eval.acord import ACORD_DIR, RELEVANCE_FLOOR, load_corpus, load_test_queries

SIDECAR_ROOT = Path("data/acord/chunk_text")
OUT_PATH = Path("data/eval/okf_gold.json")
DEBUG_FRACTION = 0.2  # fraction of each category's queries reserved as the (held-out-of-headline) debug split


class QueryGold(BaseModel):
    """One test query's gold: the chunk_ids of its relevant clauses, its induced category, its split."""

    query_id: str
    text: str
    category: Optional[str]
    split: Literal["debug", "held_out"]
    gold_chunk_ids: list[str]
    gold_corpus_ids: list[str]


class OkfGold(BaseModel):
    """The full gold-label artifact for the ACORD test-split population (T33's 57 queries)."""

    corpus: str = "acord"
    relevance_floor: int
    debug_fraction: float
    queries: dict[str, QueryGold]
    unmapped_corpus_ids: list[str]  # gold corpus-ids not in the corpus or not ingested (reported)
    induced_category_labels: dict[str, str]  # chunk_id -> silver category (from its relevant query)

    @property
    def distinct_gold_chunks(self) -> int:
        return len({cid for q in self.queries.values() for cid in q.gold_chunk_ids})

    @property
    def debug_query_ids(self) -> list[str]:
        return sorted(qid for qid, q in self.queries.items() if q.split == "debug")


# --- pure helpers -------------------------------------------------------------------------------


def map_corpus_ids(
    corpus_texts: dict[str, str], corpus_ids: Iterable[str]
) -> tuple[dict[str, str], list[str]]:
    """Map corpus-ids to `chunk_id`s via `ChunkId.of(cid, 0, text)`. Missing corpus-ids are reported."""
    mapped: dict[str, str] = {}
    unmapped: set[str] = set()
    for cid in corpus_ids:
        text = corpus_texts.get(cid)
        if text is None:
            unmapped.add(cid)
            continue
        mapped[cid] = ChunkId.of(cid, 0, text).value
    return mapped, sorted(unmapped)


def any_gold(gold: Iterable[str], reached: Iterable[str]) -> bool:
    """Any-gold reading: at least one gold chunk was reached."""
    return bool(set(gold) & set(reached))


def all_gold(gold: Iterable[str], reached: Iterable[str]) -> bool:
    """All-gold reading: every gold chunk was reached (an empty gold set is never all-reached)."""
    g = set(gold)
    return bool(g) and g <= set(reached)


def select_debug_split(
    query_categories: dict[str, Optional[str]], *, fraction: float
) -> frozenset[str]:
    """Deterministic, stratified-by-category debug split: floor(fraction * n) per category, sorted.

    Floor (not ceil) so a category with a single query stays in the held-out set, keeping every category
    represented at the GATE-3 headline. No randomness, so the split is reproducible across rebuilds.
    """
    by_cat: dict[str, list[str]] = defaultdict(list)
    for qid, cat in query_categories.items():
        by_cat[cat or "\x00None"].append(qid)
    debug: set[str] = set()
    for qids in by_cat.values():
        qids_sorted = sorted(qids)
        debug.update(qids_sorted[: floor(fraction * len(qids_sorted))])
    return frozenset(debug)


# --- build --------------------------------------------------------------------------------------


def read_query_categories(acord_dir: Path = ACORD_DIR) -> dict[str, Optional[str]]:
    """Read `metadata.category` per query (the ACORD loader drops it). Tolerates a dict or a JSON string."""
    cats: dict[str, Optional[str]] = {}
    with (acord_dir / "queries.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
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
            if not isinstance(meta, dict):
                meta = {}
            cats[str(row["_id"])] = meta.get("category")
    return cats


def build_okf_gold(
    acord_dir: Path = ACORD_DIR,
    sidecar_root: Path = SIDECAR_ROOT,
    *,
    floor: int = RELEVANCE_FLOOR,
    debug_fraction: float = DEBUG_FRACTION,
) -> OkfGold:
    """Build the gold-label artifact over the ACORD test-split population, validating against the sidecar."""
    queries = load_test_queries(acord_dir, floor=floor)  # the T33 recall population
    corpus_texts = {c.clause_id: c.text for c in load_corpus(acord_dir)}
    categories = read_query_categories(acord_dir)
    store = ChunkTextStore(sidecar_root)

    population_categories = {q.query_id: categories.get(q.query_id) for q in queries}
    debug = select_debug_split(population_categories, fraction=debug_fraction)

    unmapped: set[str] = set()
    induced: dict[str, str] = {}
    query_golds: dict[str, QueryGold] = {}
    for q in queries:
        mapped, missing = map_corpus_ids(corpus_texts, q.relevant)
        unmapped.update(missing)
        category = categories.get(q.query_id)
        gold_chunk_ids: list[str] = []
        gold_corpus_ids: list[str] = []
        for corpus_id, chunk_id in mapped.items():
            if store.get(chunk_id) is None:  # in the corpus but not ingested
                unmapped.add(corpus_id)
                continue
            gold_chunk_ids.append(chunk_id)
            gold_corpus_ids.append(corpus_id)
            if category is not None:
                # deterministic single label if a chunk is gold for queries of different categories
                prior = induced.get(chunk_id)
                induced[chunk_id] = min(prior, category) if prior else category
        query_golds[q.query_id] = QueryGold(
            query_id=q.query_id,
            text=q.text,
            category=category,
            split="debug" if q.query_id in debug else "held_out",
            gold_chunk_ids=sorted(gold_chunk_ids),
            gold_corpus_ids=sorted(gold_corpus_ids),
        )

    return OkfGold(
        relevance_floor=floor,
        debug_fraction=debug_fraction,
        queries=query_golds,
        unmapped_corpus_ids=sorted(unmapped),
        induced_category_labels=dict(sorted(induced.items())),
    )


def main() -> None:
    gold = build_okf_gold()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(gold.model_dump_json(indent=2), encoding="utf-8")

    n_debug = len(gold.debug_query_ids)
    by_cat: dict[str, int] = defaultdict(int)
    for q in gold.queries.values():
        by_cat[q.category or "None"] += 1
    print(f"[okf_gold] population: {len(gold.queries)} test queries, "
          f"{gold.distinct_gold_chunks} distinct gold chunks (grade >= {gold.relevance_floor})")
    print(f"[okf_gold] split: {n_debug} debug (held out) / {len(gold.queries) - n_debug} held-out headline")
    print(f"[okf_gold] unmapped gold corpus-ids: {len(gold.unmapped_corpus_ids)}")
    print(f"[okf_gold] induced category labels: {len(gold.induced_category_labels)} chunks over "
          f"{len(by_cat)} categories")
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"           {n:3d} queries  {cat}")
    print(f"[okf_gold] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
