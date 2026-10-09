"""PS-8a (G21): pack code reaches the store only through the generic primitives (`kg_read` / `kg_write` / `kg_edges` /
`kg_count` / `kg_delete` / `kg_update` / ...), never through the backend's raw query, command or driver handle."""
from __future__ import annotations

import re
from pathlib import Path

_RAW = re.compile(r"\._query\(|\._command\(|\._db\b")


def test_no_pack_module_uses_raw_store_access():
    root = Path(__file__).resolve().parents[2] / "src" / "rag_wright" / "packs"
    offenders = [f"{p.relative_to(root)}:{i}" for p in root.rglob("*.py")
                 for i, line in enumerate(p.read_text().splitlines(), 1) if _RAW.search(line)]
    assert offenders == []
