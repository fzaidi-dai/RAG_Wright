"""T49 (FR-K.3): embedding-free cross-linking of the OKF bundle from shared distinctive terms.

Writes standard markdown links (absolute from the bundle root) between related clauses, so a traversal can
move laterally, not only down the category tree. Edges are derived from MEASURED structure, not guessed, and
kept embedding-free (Option 1, 2026-07-22): two clauses are related when their bodies share distinctive (rare,
legal) terms; the embedding-kNN structure T41 measured is the held fallback if this does not lift the ceiling.

Density is bounded two ways so the graph does not degenerate into near-complete connectivity: an ultra-common
term (appearing in more than `MAX_TERM_DF` clauses) carries no signal and is skipped, and each clause keeps at
most `max_degree` neighbours, ranked by shared-term count. The pass is idempotent and runs INDEPENDENTLY of the
compile (it rewrites only each concept's `## Related clauses` section), so a link recipe can be re-run or
ablated without a full recompile. A concept's clause text stays the leading body verbatim; links are appended.

Run: uv run python -m rag_wright.okf.links
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from rag_wright.okf.document import parse_okf, serialize_okf

LINKS_RECIPE_VERSION = "okf-links-lex-v1"
DEFAULT_MAX_DEGREE = 8
DEFAULT_MIN_SHARED = 2  # a link needs >=2 shared distinctive terms (one rare word alone is not relatedness)
MAX_TERM_DF = 100  # a distinctive term in more than this many clauses is boilerplate -> no signal, skipped
_DISTINCTIVE_LEN = 8  # only long/rare tokens count as relatedness signal
_STEM = 6
_RELATED_HEADING = "## Related clauses"
_INDEX = "index.md"
_MANIFEST = ".okf_links.json"
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

_STOPWORDS = {
    "agreement", "including", "pursuant", "provided", "otherwise", "applicable", "hereunder", "represents",
    "respective", "obligations", "termination", "notwithstanding", "thereunder", "thereafter", "hereinafter",
}


class LinksManifest(BaseModel):
    """What the cross-link pass wrote: recipe params + edge-graph shape, stamped for attributability."""

    recipe_version: str
    max_degree: int
    min_shared: int
    n_concepts: int
    n_edges: int  # directed edges written (sum of out-degrees)
    mean_degree: float
    isolated: int  # concepts with no related link


def _terms(text: str) -> set[str]:
    """Distinctive-term stems of a clause body: long, non-boilerplate tokens reduced to a prefix stem."""
    toks = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= _DISTINCTIVE_LEN and t not in _STOPWORDS]
    return {t[:_STEM] for t in toks}


class _Concept(BaseModel):
    chunk_id: str
    rel_path: str  # bundle-root-relative, e.g. "indemnification/abc.md"
    title: str
    description: str
    terms: set[str]

    model_config = {"arbitrary_types_allowed": True}


def _clause_body(body: str) -> str:
    """The clause text (no trailing newlines) with any prior `## Related clauses` section stripped.

    Returned without a trailing newline so re-linking is byte-idempotent regardless of whether a prior
    section was present.
    """
    idx = body.find(_RELATED_HEADING)
    return (body[:idx] if idx != -1 else body).rstrip("\n")


def _load_concepts(bundle_root: Path) -> list[_Concept]:
    concepts: list[_Concept] = []
    for md in sorted(bundle_root.rglob("*.md")):
        if md.name in (_INDEX, "log.md"):
            continue
        fm, body = parse_okf(md.read_text(encoding="utf-8"))
        chunk_id = fm.get("chunk_id")
        if not chunk_id:
            continue
        concepts.append(
            _Concept(
                chunk_id=str(chunk_id),
                rel_path=str(md.relative_to(bundle_root)),
                title=str(fm.get("title") or md.stem),
                description=str(fm.get("description") or ""),
                terms=_terms(_clause_body(body)),
            )
        )
    return concepts


def build_edges(
    concepts: list[_Concept], *, max_degree: int = DEFAULT_MAX_DEGREE, min_shared: int = DEFAULT_MIN_SHARED
) -> dict[str, list[str]]:
    """chunk_id -> up to `max_degree` related chunk_ids (>= min_shared shared distinctive terms), by count."""
    term_index: dict[str, list[str]] = defaultdict(list)
    for c in concepts:
        for t in c.terms:
            term_index[t].append(c.chunk_id)

    shared: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ids in term_index.values():
        if len(ids) > MAX_TERM_DF:  # boilerplate term -> no relatedness signal, and bounds the pair blowup
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                shared[a][b] += 1
                shared[b][a] += 1

    edges: dict[str, list[str]] = {}
    for c in concepts:
        ranked = sorted(
            ((nbr, cnt) for nbr, cnt in shared.get(c.chunk_id, {}).items() if cnt >= min_shared),
            key=lambda nc: (-nc[1], nc[0]),
        )
        edges[c.chunk_id] = [nbr for nbr, _ in ranked[:max_degree]]
    return edges


def _related_section(neighbours: list[str], by_id: dict[str, _Concept]) -> str:
    lines = [_RELATED_HEADING, ""]
    for nbr in neighbours:
        c = by_id[nbr]
        suffix = f" - {c.description}" if c.description else ""
        lines.append(f"* [{c.title}](/{c.rel_path}){suffix}")  # absolute from bundle root, per OKF §5
    return "\n".join(lines) + "\n"


def apply_links(
    bundle_root: Path,
    *,
    max_degree: int = DEFAULT_MAX_DEGREE,
    min_shared: int = DEFAULT_MIN_SHARED,
    recipe_version: str = LINKS_RECIPE_VERSION,
) -> LinksManifest:
    """Build edges and write each concept's `## Related clauses` section. Idempotent; independent of compile."""
    bundle_root = Path(bundle_root)
    concepts = _load_concepts(bundle_root)
    by_id = {c.chunk_id: c for c in concepts}
    edges = build_edges(concepts, max_degree=max_degree, min_shared=min_shared)

    n_edges = 0
    isolated = 0
    for c in concepts:
        neighbours = edges.get(c.chunk_id, [])
        n_edges += len(neighbours)
        isolated += 1 if not neighbours else 0
        path = bundle_root / c.rel_path
        fm, body = parse_okf(path.read_text(encoding="utf-8"))
        clause = _clause_body(body)  # no trailing newline
        new_body = f"{clause}\n\n{_related_section(neighbours, by_id)}" if neighbours else f"{clause}\n"
        path.write_text(serialize_okf(fm, new_body), encoding="utf-8")

    manifest = LinksManifest(
        recipe_version=recipe_version,
        max_degree=max_degree,
        min_shared=min_shared,
        n_concepts=len(concepts),
        n_edges=n_edges,
        mean_degree=(n_edges / len(concepts)) if concepts else 0.0,
        isolated=isolated,
    )
    (bundle_root / _MANIFEST).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def _default_bundle() -> Path:
    return Path("data/acord/okf/bundle")


def main() -> None:
    manifest = apply_links(_default_bundle())
    print(f"[okf_links] recipe={manifest.recipe_version} concepts={manifest.n_concepts} "
          f"edges={manifest.n_edges} mean_degree={manifest.mean_degree:.2f} isolated={manifest.isolated}")


if __name__ == "__main__":
    main()
