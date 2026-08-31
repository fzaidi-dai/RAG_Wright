"""ADR-0066 P1b-1 gate: the extraction template's schema + knowledge is captured FAITHFULLY in the ttl.

`contract_bridge.ttl` now describes every field of `clause_template.py` (kind, default, enum/model ref, edge,
max_length, order, the LOOK-FOR description, and the examples). This test fails on ANY drift -- a template edit
not re-captured, OR a hand-edit of the ttl capture -- so after P1b-2 (the template generated FROM the ttl) the two
can never silently diverge. To fix a failure BEFORE P1b-2: edit the template, then
`uv run python scripts/bootstrap_template_capture.py`. AFTER P1b-2: edit the ttl and regenerate the template.
"""

from __future__ import annotations

from rag_wright.ontology.loader import load_template_fields
from rag_wright.ontology.template_introspect import introspect_template_fields


def test_ttl_captures_every_template_field_faithfully() -> None:
    assert load_template_fields() == introspect_template_fields()


def test_all_43_fields_are_present() -> None:
    # a coverage backstop: the root Clause (35) + the 3 nested constraint models (3+3+2) = 43 fields.
    assert len(load_template_fields()) == 43
