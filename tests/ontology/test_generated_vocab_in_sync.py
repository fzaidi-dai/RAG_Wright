"""ADR-0066 Phase 1 gate: the generated Python vocab is in sync with the ttl (the CI drift-diff).

`_generated_vocab.py` is GENERATED FROM contract_bridge.ttl. This test fails the build on ANY drift -- a ttl edit
that was not regenerated, OR a hand-edit of the generated file -- so the ontology can never silently diverge from
the code and truth stays in the ttl (ADR-0066 Rule 2). To fix a failure: edit the ttl (never the generated file),
then `uv run python scripts/generate_contract_python.py`.
"""

from __future__ import annotations

from rag_wright.ontology.codegen import VOCAB_MODULE_PATH, render_vocab_module


def test_generated_vocab_matches_a_fresh_render_from_the_ttl() -> None:
    committed = VOCAB_MODULE_PATH.read_text(encoding="utf-8")
    assert committed == render_vocab_module(), (
        "src/rag_wright/ontology/_generated_vocab.py is STALE or hand-edited -- regenerate it from the ttl: "
        "uv run python scripts/generate_contract_python.py")


def test_generation_is_idempotent() -> None:
    # rendering twice yields identical text (deterministic: sorted) -> `uv run <gen>` never churns the diff.
    assert render_vocab_module() == render_vocab_module()
