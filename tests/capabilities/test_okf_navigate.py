"""T50 (FR-K.5/K.6, RAC-50): tests for the okf_navigate traversal capability.

Hermetic: the deterministic navigation primitives run over a tiny hand-built bundle, and the capability
plumbing (dedup, bounds, telemetry, trace, result contract) is exercised through a STUB navigator -- no model,
no interpreter. The live interpreter-driven traversal quality is an opt-in `-m model` test, since branch
choice is model-dependent and a stub cannot exercise it.
"""

from __future__ import annotations

from pathlib import Path

from rag_wright.capabilities.okf_navigate import (
    Bounds,
    NavigationResult,
    OkfBundleReader,
    Telemetry,
    TraceStep,
    _parse_shortlist,
    okf_navigate,
    register_okf_navigate,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.okf.document import serialize_okf


def _bundle(root: Path) -> Path:
    (root / "index.md").write_text(
        serialize_okf({"okf_version": "0.1"}, "# Categories\n\n* [governing-law](governing-law/index.md) - law clauses\n"),
        encoding="utf-8",
    )
    d = root / "governing-law"
    d.mkdir()
    (d / "index.md").write_text("# Clauses\n\n* [g1](g1.md) - New York governing law\n", encoding="utf-8")
    (d / "g1.md").write_text(
        serialize_okf(
            {"type": "Clause", "title": "g1", "description": "NY law", "chunk_id": "g1:0:abc", "source_doc_id": "g1"},
            "The laws of New York govern.\n\n## Related clauses\n\n* [g2](/governing-law/g2.md) - related\n",
        ),
        encoding="utf-8",
    )
    (d / "g2.md").write_text(
        serialize_okf({"type": "Clause", "title": "g2", "description": "DE law", "chunk_id": "g2:0:def"}, "Delaware law."),
        encoding="utf-8",
    )
    return root


# --- primitives ---------------------------------------------------------------------------------


def test_read_index_resolves_signpost_paths(tmp_path):
    reader = OkfBundleReader(_bundle(tmp_path))
    root = reader.read_index("")
    assert root[0].path == "governing-law" and root[0].is_dir  # dir link resolved, /index.md stripped
    assert root[0].description == "law clauses"
    clauses = reader.read_index("governing-law")
    # concept link "g1.md" resolved to the root-relative path (dir prefix applied in Python, not JS)
    assert clauses[0].path == "governing-law/g1.md" and not clauses[0].is_dir


def test_read_body_strips_related_and_frontmatter(tmp_path):
    reader = OkfBundleReader(_bundle(tmp_path))
    body = reader.read_body("governing-law/g1.md")
    assert body == "The laws of New York govern."  # clause only: no frontmatter, no Related section


def test_related_and_concept_id(tmp_path):
    reader = OkfBundleReader(_bundle(tmp_path))
    # cross-links resolve to root-relative bundle paths (from our "## Related clauses" section)
    assert reader.related("governing-law/g1.md") == ["governing-law/g2.md"]
    assert reader.concept_id("governing-law/g1.md") == "g1:0:abc"  # frontmatter chunk_id
    assert reader.concept_id("governing-law/g2.md") == "g2:0:def"


def test_related_is_generic_over_inline_links(tmp_path):
    # a bundle whose cross-links are INLINE (no "## Related clauses" section) -- a generic OKF bundle
    root = tmp_path
    d = root / "tables"
    d.mkdir()
    (d / "index.md").write_text("# Tables\n\n* [orders](orders.md) - order table\n", encoding="utf-8")
    (d / "orders.md").write_text(
        serialize_okf({"type": "BigQuery Table", "chunk_id": "orders"},
                      "Joined with [customers](/tables/customers.md) on id; see [neighbor](./events.md).\n"),
        encoding="utf-8",
    )
    reader = OkfBundleReader(root)
    # both an absolute-from-root and a relative inline link are found, resolved, deduped
    assert set(reader.related("tables/orders.md")) == {"tables/customers.md", "tables/events.md"}


# --- capability plumbing (stub navigator) -------------------------------------------------------


class _StubNavigator:
    def navigate(self, query, reader, bounds):
        # returns a duplicate to prove dedup, and a trace/telemetry to prove they are carried through
        trace = [TraceStep(depth=0, action="read_index", path="", detail="root")]
        return ["g1:0:abc", "g2:0:def", "g1:0:abc"], trace, Telemetry(serial_rounds=2, bodies_read=2, dispatches=4)


def test_okf_navigate_dedups_and_wraps(tmp_path):
    result = okf_navigate("New York law", _bundle(tmp_path), navigator=_StubNavigator(), bounds=Bounds(frontier_budget=10))
    assert isinstance(result, NavigationResult)
    assert result.shortlist == ["g1:0:abc", "g2:0:def"]  # deduped, order preserved
    assert result.bounds.frontier_budget == 10  # run params recorded
    assert result.telemetry.bodies_read == 2
    assert result.trace[0].action == "read_index"


# --- result parsing -----------------------------------------------------------------------------


def test_parse_shortlist_extracts_result():
    # the model's workflow returns JSON.stringify({shortlist}); parse it out of surrounding noise
    assert _parse_shortlist('noise {"shortlist": ["g1:0:abc", "g2:0:def"]} trailing') == ["g1:0:abc", "g2:0:def"]


def test_parse_shortlist_tolerates_garbage():
    assert _parse_shortlist("no json here") == []  # never raises


# --- registration -------------------------------------------------------------------------------


def test_okf_navigate_registers_under_canonical_slug():
    registry = CapabilityRegistry()
    register_okf_navigate(registry)
    assert registry.get("okf_navigate").name == "okf_navigate"
