"""ADR-0066 Phase 1: GENERATE the Python contract vocabulary FROM the ontology `.ttl` (the source of truth).

The ttl is authoritative (ADR-0066); this reverses the old arrow -- the Python is now generated. Run after any
edit to `contract_bridge.ttl` to regenerate `src/rag_wright/ontology/_generated_vocab.py`. The committed generated
file MUST always equal a fresh run: `tests/ontology/test_generated_vocab_in_sync.py` fails the build on any drift
(a ttl edit not regenerated, OR a hand-edit of the generated file) -- so staleness is impossible by construction,
never solved by moving truth back into code (ADR-0066 Rule 2).

Usage: uv run python scripts/generate_contract_python.py
"""

from __future__ import annotations

from rag_wright.ontology.codegen import (
    TEMPLATE_META_MODULE_PATH,
    VOCAB_MODULE_PATH,
    render_template_meta_module,
    render_vocab_module,
)


def main() -> None:
    VOCAB_MODULE_PATH.write_text(render_vocab_module(), encoding="utf-8")
    print(f"wrote {VOCAB_MODULE_PATH}")
    TEMPLATE_META_MODULE_PATH.write_text(render_template_meta_module(), encoding="utf-8")
    print(f"wrote {TEMPLATE_META_MODULE_PATH}")


if __name__ == "__main__":
    main()
