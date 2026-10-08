"""ENG-2: `build_partial_entry` is a STABLE, PUBLIC engine API.

RuleWright (product) imports it and calls it through their seam so partial-loss semantics have a SINGLE
definition that cannot drift between engine and product (the drift that caused the 0006-C `span_failures`
integrator trap). This test PINS that contract -- the import path, the signature, and the shape of the
`failures` list. Breaking any assertion here is a breaking change for every integrator and needs a deliberate,
versioned decision, never an incidental refactor.

Forward-compat rule (relied on by the product's `total_failures` count): a NEW loss kind is a new `kind` value
inside `failures`, never a replacement top-level key -- so an integrator counting the kind-tagged list keeps
surfacing losses it has no dedicated field for.
"""

from __future__ import annotations

import inspect

# the stable, public import path -- do not move without a versioned decision (integrators import from here)
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import build_partial_entry


def test_signature_is_stable():
    # ocr_failures (0009-WIRE2) and graph_failures (PS-R2) are OPTIONAL trailing args -> 3-positional callers (the
    # product) are unaffected
    params = list(inspect.signature(build_partial_entry).parameters)
    assert params == ["source_doc_id", "clause_failures", "span_failures", "ocr_failures", "graph_failures"]
    assert inspect.signature(build_partial_entry).parameters["ocr_failures"].default is None
    assert inspect.signature(build_partial_entry).parameters["graph_failures"].default is None


def test_new_graph_kind_flows_through_the_unified_failures_list():
    # PS-R2: a failed party/graph extraction no longer dead-letters the document; it is a `graph` loss in `failures`
    entry = build_partial_entry("D1", [], [], graph_failures=[{"stage": "extract_graph", "reason": "timeout"}])
    assert entry["failures"] == [{"kind": "graph", "stage": "extract_graph", "reason": "timeout"}]
    assert entry["graph_failures"] == [{"stage": "extract_graph", "reason": "timeout"}]


def test_new_ocr_kind_flows_through_the_unified_failures_list():
    # FORWARD-COMPAT: a new loss kind (ocr) arrives as a new `kind` in `failures` -- an integrator counting the
    # kind-tagged list surfaces it with no code change; a 3-arg caller never passes it.
    entry = build_partial_entry("D1", [], [], [{"page": 3, "reason": "unreadable scan"}])
    assert entry["failures"] == [{"kind": "ocr", "page": 3, "reason": "unreadable scan"}]
    assert entry["ocr_failures"] == [{"page": 3, "reason": "unreadable scan"}]
    assert "clause_failures" not in entry and "span_failures" not in entry
    assert build_partial_entry("D1", [], [], []) is None  # no loss of any kind -> not partial


def test_no_loss_returns_none():
    assert build_partial_entry("D1", [], []) is None
    assert build_partial_entry("D1", None, None) is None  # tolerant of missing lists (a complete doc is not partial)


def test_failures_is_always_present_and_kind_tagged():
    entry = build_partial_entry(
        "D1",
        [{"span_id": "D1#1", "reason": "trunc"}],
        [{"span_id": "D1#4", "reason": "sql"}])
    assert entry["source_doc_id"] == "D1"
    assert entry["failures"] == [                                      # ALWAYS present; clause first, then span
        {"kind": "clause", "span_id": "D1#1", "reason": "trunc"},
        {"kind": "span", "span_id": "D1#4", "reason": "sql"}]
    assert entry["clause_failures"] == [{"span_id": "D1#1", "reason": "trunc"}]  # back-compat keys, present per-kind
    assert entry["span_failures"] == [{"span_id": "D1#4", "reason": "sql"}]


def test_span_only_loss_omits_the_clause_key_but_failures_still_shows_it():
    entry = build_partial_entry("D1", [], [{"span_id": "D1#4", "reason": "sql"}])
    assert "clause_failures" not in entry                             # the trap: this per-kind key is absent
    assert entry["failures"] == [{"kind": "span", "span_id": "D1#4", "reason": "sql"}]  # but `failures` is not


def test_clause_only_loss_omits_the_span_key():
    entry = build_partial_entry("D1", [{"span_id": "D1#1", "reason": "trunc"}], [])
    assert "span_failures" not in entry
    assert [f["kind"] for f in entry["failures"]] == ["clause"]


def test_extra_failure_detail_flows_through_the_unified_list():
    # forward-compat: a failure dict richer than {span_id, reason} keeps its extra fields in the unified entry,
    # so the engine can add per-failure detail without breaking the kind-tagged contract.
    entry = build_partial_entry("D1", [], [{"span_id": "D1#4", "reason": "sql", "attempt": 3}])
    assert entry["failures"][0] == {"kind": "span", "span_id": "D1#4", "reason": "sql", "attempt": 3}


def test_inputs_are_not_mutated():
    # the caller's lists must be left untouched (the product reuses them for its own accounting)
    cf = [{"span_id": "D1#1", "reason": "trunc"}]
    sf = [{"span_id": "D1#4", "reason": "sql"}]
    build_partial_entry("D1", cf, sf)
    assert cf == [{"span_id": "D1#1", "reason": "trunc"}] and sf == [{"span_id": "D1#4", "reason": "sql"}]
