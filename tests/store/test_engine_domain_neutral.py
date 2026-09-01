"""ADR-0067 P5c scope guard: the engine's KG STORAGE + the entity-resolution INTERFACE must not assume the
SEC/EDGAR corpus. The SEC resolver (corpus/edgar.py) and the CUAD/EDGAR corpus adapters are the plug-in layer
(exempt) -- a new domain supplies its own resolver + registry. This fails the build if a `cik`/`edgar` reference
creeps back into the generic storage/resolver, re-coupling the engine to SEC.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "rag_wright"
# The engine's domain-neutral storage + resolver interface (must be SEC-free). The SEC resolver + corpus adapters
# (corpus/edgar.py, corpus/cuad_ingestion.py, capabilities/dg_extraction.py) are the plug-in, intentionally NOT here.
_ENGINE_MODULES = (
    "store/arcadedb.py",
    "store/seam.py",
    "capabilities/graph_storage.py",
    "capabilities/entity_resolution.py",
    "ontology/registry.py",  # ADR-0067 generic registry: SEC-free (the EDGAR builder is in corpus/edgar.py)
)
_SEC = re.compile(r"\b(cik|edgar)\b", re.IGNORECASE)


def test_engine_storage_and_resolver_are_sec_free() -> None:
    offenders: dict[str, list[str]] = {}
    for rel in _ENGINE_MODULES:
        hits = [ln.strip() for ln in (_SRC / rel).read_text(encoding="utf-8").splitlines() if _SEC.search(ln)]
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "ADR-0067 P5c: the engine storage/resolver must stay SEC-free (the SEC resolver + adapters are the "
        f"plug-in layer). Re-coupled references: {offenders}")
