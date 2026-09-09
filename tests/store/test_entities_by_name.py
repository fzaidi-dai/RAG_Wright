"""issue 0030 / ADR-0093: `entities_by_name` resolves a party NAME to its graph entities on the Store seam.
It normalizes with the engine's own `normalize_entity_name` (the SAME key ingestion clusters on), so name
variants collapse to one clustering key and the caller never re-implements the rule. A name may resolve to
SEVERAL nodes (e.g. a resolved node plus a not-yet-merged unlinked ref) -- all are returned, each usable as
`graph_neighbors`' `start_entity_id`. Hermetic -- `_query` stubbed, no live DB."""

from __future__ import annotations

from rag_wright.store.arcadedb import ArcadeDBStore

# what the store holds: a linked public company + an unlinked ref for the SAME real-world party (both key
# "acme"), plus two distinct unlinked parties.
_ROWS = [
    {"entity_id": "us-edgar:0000320193", "name": "Acme Corporation", "entity_type": "Organization"},
    {"entity_id": "UNLINKED:acme", "name": "Acme Corp.", "entity_type": "Organization"},
    {"entity_id": "UNLINKED:beta", "name": "Beta LLC", "entity_type": "Organization"},
    {"entity_id": "UNLINKED:gamma", "name": "Gamma Partners", "entity_type": "Organization"},
]


def _bare_store():
    s = object.__new__(ArcadeDBStore)
    s._query = lambda sql: [dict(r) for r in _ROWS] if "FROM Entity" in sql else []
    return s


def test_resolves_name_to_all_matching_entities():
    store = _bare_store()
    # "Acme Corp" normalizes to "acme" -- both the linked node and the unlinked ref share that key
    hits = store.entities_by_name("Acme Corp")
    assert {h["entity_id"] for h in hits} == {"us-edgar:0000320193", "UNLINKED:acme"}
    assert all(set(h) == {"entity_id", "name", "entity_type"} for h in hits)  # exactly the three fields


def test_variants_and_casing_collapse_to_the_same_key():
    store = _bare_store()
    for variant in ("ACME, INC.", "acme corporation", "Acme, Incorporated"):
        assert {h["entity_id"] for h in store.entities_by_name(variant)} == {"us-edgar:0000320193", "UNLINKED:acme"}


def test_already_normalized_input_is_idempotent():
    store = _bare_store()
    # passing the already-normalized clustering key still resolves (normalize is idempotent)
    assert {h["entity_id"] for h in store.entities_by_name("acme")} == {"us-edgar:0000320193", "UNLINKED:acme"}


def test_unlinked_party_resolvable_by_name():
    store = _bare_store()
    assert {h["entity_id"] for h in store.entities_by_name("Beta, LLC")} == {"UNLINKED:beta"}


def test_no_match_returns_empty_not_error():
    store = _bare_store()
    assert store.entities_by_name("Nonexistent Holdings") == []


def test_empty_or_whitespace_name_returns_empty():
    store = _bare_store()
    assert store.entities_by_name("") == []
    assert store.entities_by_name("   ") == []
