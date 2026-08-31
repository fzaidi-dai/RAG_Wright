"""ADR-0066 Phase 1: render the GENERATED Python contract vocabulary FROM the ontology `.ttl`.

The single place the ttl -> Python generation lives (imported by scripts/generate_contract_python.py to WRITE the
file, and by tests/ontology/test_generated_vocab_in_sync.py to ASSERT the committed file equals a fresh render).
The ttl is the source of truth (ADR-0066); the generated file is regenerable and CI-diff-enforced, never hand-
edited -- staleness is impossible by construction, not solved by moving truth back into code (Rule 2)."""

from __future__ import annotations

from pathlib import Path

from rag_wright.ontology.loader import load_contract_ontology

VOCAB_MODULE_PATH = Path(__file__).with_name("_generated_vocab.py")

_HEADER = '''"""GENERATED FROM contract_bridge.ttl by scripts/generate_contract_python.py -- DO NOT EDIT BY HAND.

ADR-0066: the ontology `.ttl` is the single source of truth. To change the closed vocabulary, edit the ttl and
re-run the generator; a hand-edit here (or a stale regeneration) is caught by
tests/ontology/test_generated_vocab_in_sync.py."""

from __future__ import annotations

'''


def render_vocab_module() -> str:
    """The exact text the generated vocab module must contain (deterministic: sorted dimensions + values)."""
    view = load_contract_ontology()
    lines = [_HEADER, "VOCAB: dict[str, frozenset[str]] = {"]
    for dim in sorted(view.closed_vocab):
        values = ", ".join(repr(v) for v in sorted(view.closed_vocab[dim]))
        lines.append(f"    {dim!r}: frozenset({{{values}}}),")
    lines.append("}")
    return "\n".join(lines) + "\n"
