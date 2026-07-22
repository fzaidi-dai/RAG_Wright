"""T49 (FR-K.3, RAC-49): tests for the embedding-free cross-linker.

Hermetic: a tiny bundle of concept files whose bodies share distinctive terms. Pins the edge rules (min-shared
threshold, bounded degree), the absolute-markdown link format, idempotent regeneration, clause-body preservation,
and broken-link tolerance.
"""

from __future__ import annotations

from pathlib import Path

from rag_wright.okf.document import parse_okf, serialize_okf
from rag_wright.okf.lint import lint_bundle
from rag_wright.okf.links import apply_links

# a & b share 3 distinctive terms; d shares only 1 (confidentiality); c shares none.
_BODIES = {
    ("indemnification", "aaa"): "The Supplier shall provide indemnification, confidentiality and arbitration.",
    ("indemnification", "bbb"): "Confidentiality and arbitration govern indemnification disputes here.",
    ("indemnification", "ddd"): "Confidentiality is preserved in this short provision.",
    ("governing-law", "ccc"): "Delaware jurisdiction chosen; short and unrelated.",
}


def _write_bundle(root: Path) -> None:
    for (category, sdi), body in _BODIES.items():
        d = root / category
        d.mkdir(parents=True, exist_ok=True)
        fm = {
            "type": "Clause", "title": sdi, "description": f"desc for {sdi}",
            "tags": [category], "category": category,
            "chunk_id": f"{sdi}:0:{'a' * 64}", "source_doc_id": sdi,
        }
        (d / f"{sdi}.md").write_text(serialize_okf(fm, body), encoding="utf-8")


def _related_targets(concept: Path, root: Path) -> list[str]:
    _, body = parse_okf(concept.read_text(encoding="utf-8"))
    if "## Related clauses" not in body:
        return []
    section = body.split("## Related clauses", 1)[1]
    import re
    return [m.group(1) for m in re.finditer(r"\]\(([^)]+)\)", section)]


def test_related_links_are_absolute_and_resolve(tmp_path):
    _write_bundle(tmp_path)
    apply_links(tmp_path, min_shared=2, max_degree=8)
    targets = _related_targets(tmp_path / "indemnification" / "aaa.md", tmp_path)
    assert "/indemnification/bbb.md" in targets  # a links b (3 shared terms), absolute from root
    assert all(t.startswith("/") for t in targets)  # absolute, per OKF §5
    assert lint_bundle(tmp_path).broken_link_ratio == 0.0  # every written link resolves


def test_min_shared_excludes_weak_links(tmp_path):
    _write_bundle(tmp_path)
    apply_links(tmp_path, min_shared=2, max_degree=8)
    # d shares only "confidentiality" with a (1 < min_shared) -> not linked
    assert "/indemnification/ddd.md" not in _related_targets(tmp_path / "indemnification" / "aaa.md", tmp_path)
    # c shares nothing -> isolated
    assert _related_targets(tmp_path / "governing-law" / "ccc.md", tmp_path) == []


def test_max_degree_bounds_density(tmp_path):
    _write_bundle(tmp_path)
    apply_links(tmp_path, min_shared=2, max_degree=1)  # a & b are mutually related; cap to 1
    assert len(_related_targets(tmp_path / "indemnification" / "aaa.md", tmp_path)) <= 1


def test_idempotent_regeneration(tmp_path):
    _write_bundle(tmp_path)
    apply_links(tmp_path)
    first = (tmp_path / "indemnification" / "aaa.md").read_text(encoding="utf-8")
    apply_links(tmp_path)  # re-run: strips and rewrites the Related section
    assert (tmp_path / "indemnification" / "aaa.md").read_text(encoding="utf-8") == first


def test_clause_body_preserved_as_leading_text(tmp_path):
    _write_bundle(tmp_path)
    apply_links(tmp_path)
    _, body = parse_okf((tmp_path / "indemnification" / "aaa.md").read_text(encoding="utf-8"))
    assert body.startswith(_BODIES[("indemnification", "aaa")])  # clause text intact, links appended after


def test_dangling_link_is_tolerated(tmp_path):
    _write_bundle(tmp_path)
    apply_links(tmp_path)
    concept = tmp_path / "indemnification" / "aaa.md"
    fm, body = parse_okf(concept.read_text(encoding="utf-8"))
    concept.write_text(serialize_okf(fm, body + "\n* [gone](/indemnification/missing.md)\n"), encoding="utf-8")
    report = lint_bundle(tmp_path)  # must not fault on the dangling link
    assert report.broken_link_ratio > 0.0
