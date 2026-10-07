"""ADR-0066 P1b-2 gate: the generated template-knowledge module is in sync with the ttl (the CI drift-diff).

`_generated_template_meta.py` (the field descriptions + examples that `clause_template.py` consumes) is GENERATED
FROM contract_bridge.ttl. This fails the build on any drift -- a ttl edit not regenerated, or a hand-edit -- so
the template's knowledge can never silently diverge from the ttl (Rule 2/3). Fix: edit the ttl, then
`uv run python scripts/generate_contract_python.py`.
"""

from __future__ import annotations

from rag_wright.packs.contracts.ontology.codegen import TEMPLATE_META_MODULE_PATH, render_template_meta_module


def test_generated_template_meta_matches_a_fresh_render_from_the_ttl() -> None:
    assert TEMPLATE_META_MODULE_PATH.read_text(encoding="utf-8") == render_template_meta_module(), (
        "_generated_template_meta.py is STALE or hand-edited -- regenerate from the ttl: "
        "uv run python scripts/generate_contract_python.py")


def test_meta_generation_is_idempotent() -> None:
    assert render_template_meta_module() == render_template_meta_module()
