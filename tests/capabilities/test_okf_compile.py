"""T46 (FR-K.1/K.2/K.4, RAC-46): OKF bundle compile + enrichment gate + conformance lint.

Hermetic: a stub `ClauseClassifier` drives enrichment (no model), and the compile/lint are deterministic.
Clause text is deliberately messy (brackets, newline, embedded quotes) and a description carries a colon and
a quote, so the byte-faithful-body claim and the YAML frontmatter escaping are exercised on real-shaped text.
"""

from __future__ import annotations

import asyncio

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.okf.compile import (
    OkfBundleManifest,
    compile_bundle,
    recompile_category,
    register_okf_compile,
)
from rag_wright.okf.document import parse_okf
from rag_wright.okf.enrich import ClauseEnrichment, EnrichedClause, enrich_all
from rag_wright.okf.lint import lint_bundle

# messy: brackets, a newline, embedded double quotes
_TEXTS_RAW = {
    "aaa1": 'Liability is capped: [see 12.1].\nIn no event "consequential" damages.',
    "bbb2": "The Supplier shall indemnify and hold harmless the Buyer.",
    "ccc3": "This Agreement is governed by the laws of New York.",
    "ddd4": "Boilerplate recitals with no clear clause type at all.",
}


def _cid(sdi: str) -> str:
    return ChunkId.of(sdi, 0, _TEXTS_RAW[sdi]).value


def _texts() -> dict[str, str]:
    return {_cid(sdi): text for sdi, text in _TEXTS_RAW.items()}


class _StubClassifier:
    """Deterministic keyword classifier that also counts calls (to prove the enrichment gate)."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str) -> ClauseEnrichment:
        self.calls += 1
        low = text.lower()
        if "liability is capped" in low:
            # description carries a colon and a quote -> exercises YAML escaping
            return ClauseEnrichment(category="Limitation of Liability", description='Caps liability: no "consequential" damages.')
        if "indemnify" in low:
            return ClauseEnrichment(category="Indemnification", description="Supplier indemnifies Buyer.")
        if "governed by the laws" in low:
            return ClauseEnrichment(category="Governing Law", description="New York governing law.")
        return ClauseEnrichment(category="None of these", description="Unclassifiable boilerplate.")


def _enrich(texts: dict[str, str]) -> dict[str, EnrichedClause]:
    return asyncio.run(enrich_all(texts, _StubClassifier()))


# --- enrichment gate ----------------------------------------------------------------------------


def test_enrich_all_is_gated_by_cache():
    texts = _texts()
    clf = _StubClassifier()
    first = asyncio.run(enrich_all(texts, clf))
    assert clf.calls == len(texts)  # cold: classify all
    again = asyncio.run(enrich_all(texts, clf, cache=first))
    assert clf.calls == len(texts)  # warm: cache covers all -> no new calls
    assert again == first


def test_enrich_marks_abstention_uncategorized():
    e = _enrich(_texts())
    assert e[_cid("ddd4")].categorized is False
    assert e[_cid("aaa1")].categorized is True
    assert e[_cid("aaa1")].category == "Limitation of Liability"


# --- compile ------------------------------------------------------------------------------------


def test_concept_files_have_byte_faithful_body_and_nonempty_type(tmp_path):
    texts = _texts()
    compile_bundle(texts, _enrich(texts), tmp_path)
    concept = tmp_path / "limitation-of-liability" / "aaa1.md"
    raw = concept.read_text(encoding="utf-8")
    fm, body = parse_okf(raw)
    assert fm["type"] == "Clause"  # non-empty type
    assert _TEXTS_RAW["aaa1"] in raw  # body byte-faithful (verbatim, messy text intact)
    assert body.rstrip("\n") == _TEXTS_RAW["aaa1"]
    assert fm["chunk_id"] == _cid("aaa1")  # identifier unchanged, carried in frontmatter


def test_category_tree_and_uncategorized_fallback(tmp_path):
    texts = _texts()
    manifest = compile_bundle(texts, _enrich(texts), tmp_path)
    assert (tmp_path / "limitation-of-liability" / "aaa1.md").exists()
    assert (tmp_path / "indemnification" / "bbb2.md").exists()
    assert (tmp_path / "_uncategorized" / "ddd4.md").exists()  # abstention -> recorded fallback subtree
    assert manifest.n_categorized == 3
    assert manifest.categories["_uncategorized"] == 1


def test_indexes_carry_real_descriptions_not_restated_titles(tmp_path):
    texts = _texts()
    compile_bundle(texts, _enrich(texts), tmp_path)
    cat_index = (tmp_path / "limitation-of-liability" / "index.md").read_text(encoding="utf-8")
    assert "* [aaa1](aaa1.md) - " in cat_index
    assert "Caps liability" in cat_index  # the description, not just the title
    root_index = (tmp_path / "index.md").read_text(encoding="utf-8")
    fm, _ = parse_okf(root_index)
    assert fm["okf_version"] == "0.1"  # root records okf_version
    assert fm["compile_recipe_version"]  # ... and the recipe version


def test_content_hash_gate_does_no_work_on_recompile(tmp_path):
    texts = _texts()
    enrichment = _enrich(texts)
    first = compile_bundle(texts, enrichment, tmp_path)
    concept = tmp_path / "indemnification" / "bbb2.md"
    concept.write_text(concept.read_text(encoding="utf-8") + "\n<!-- sentinel -->\n", encoding="utf-8")
    second = compile_bundle(texts, enrichment, tmp_path)  # unchanged inputs
    assert second.content_hash == first.content_hash
    assert "<!-- sentinel -->" in concept.read_text(encoding="utf-8")  # not rewritten -> no work


def test_compile_is_deterministic(tmp_path):
    texts = _texts()
    enrichment = _enrich(texts)
    a = compile_bundle(texts, enrichment, tmp_path / "a")
    b = compile_bundle(texts, enrichment, tmp_path / "b")
    assert a.model_dump() == {**b.model_dump()}
    for rel in ("index.md", "limitation-of-liability/aaa1.md", "indemnification/index.md"):
        assert (tmp_path / "a" / rel).read_text() == (tmp_path / "b" / rel).read_text()


def test_selective_recompile_touches_one_subtree_only(tmp_path):
    texts = _texts()
    enrichment = _enrich(texts)
    compile_bundle(texts, enrichment, tmp_path)
    other = tmp_path / "indemnification" / "bbb2.md"
    other_before = other.read_text(encoding="utf-8")

    # change only the Governing Law clause's description and recompile just that subtree
    gov_cid = _cid("ccc3")
    enrichment[gov_cid] = EnrichedClause(
        chunk_id=gov_cid, category="Governing Law", description="Delaware, not New York.", categorized=True
    )
    recompile_category(tmp_path, "Governing Law", {gov_cid: texts[gov_cid]}, enrichment)

    assert "Delaware, not New York." in (tmp_path / "governing-law" / "index.md").read_text()
    assert other.read_text(encoding="utf-8") == other_before  # other subtree untouched


# --- lint ---------------------------------------------------------------------------------------


def test_lint_passes_and_reports_numbers(tmp_path):
    texts = _texts()
    compile_bundle(texts, _enrich(texts), tmp_path)
    report = lint_bundle(tmp_path)
    assert report.passes is True
    assert report.total_concepts == 4
    assert report.frontmatter_parseable == 4
    assert report.type_non_empty == 4
    assert report.broken_link_ratio == 0.0
    assert report.description_coverage == 1.0
    assert report.orphan_rate == 0.0  # every concept is linked from its category index


# --- registration -------------------------------------------------------------------------------


def test_okf_compile_registers_under_canonical_slug():
    registry = CapabilityRegistry()
    register_okf_compile(registry)
    reg = registry.get("okf_compile")
    assert reg.name == "okf_compile"
    assert reg.contract is OkfBundleManifest
