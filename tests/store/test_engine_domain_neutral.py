"""ADR-0067 P5c scope guard: the engine's KG STORAGE + the entity-resolution INTERFACE must not assume the
SEC/EDGAR corpus. The SEC resolver (packs/contracts/corpus/edgar.py) and the CUAD/EDGAR corpus adapters are the plug-in layer
(exempt) -- a new domain supplies its own resolver + registry. This fails the build if a `cik`/`edgar` reference
creeps back into the generic storage/resolver, re-coupling the engine to SEC.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "rag_wright"
# The engine's domain-neutral storage + resolver interface (must be SEC-free). The SEC resolver + corpus adapters
# (packs/contracts/corpus/edgar.py, packs/contracts/corpus/cuad_ingestion.py, packs/contracts/capabilities/dg_extraction.py) are the plug-in, intentionally NOT here.
_ENGINE_MODULES = (
    "store/arcadedb.py",
    "store/seam.py",
    "capabilities/graph_storage.py",
    "capabilities/entity_resolution.py",
    "ontology/registry.py",  # ADR-0067 generic registry: SEC-free (the EDGAR builder is in packs/contracts/corpus/edgar.py)
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


# ADR-0117 DD-1b/DD-1c: all domain writes + jurisdiction canonicalization moved to the capability-layer store
# extensions (ContractKGStore / ComplianceStore). The engine store may import ONLY the generic engine contracts
# (the retrieval-index chunk/span records + the provenance tag) -- any DOMAIN contract import re-couples it.
_GENERIC_ENGINE_CONTRACTS = {"chunk", "provenance", "span"}


def test_engine_store_imports_only_generic_contracts() -> None:
    src = (_SRC / "store" / "arcadedb.py").read_text(encoding="utf-8")
    imported = set(re.findall(r"from rag_wright\.contracts\.([a-z_]+) import", src))
    domain = sorted(imported - _GENERIC_ENGINE_CONTRACTS)
    assert not domain, (
        "ADR-0117 DD-1c: store/arcadedb.py must import no DOMAIN contract (only chunk/provenance/span). The clause-KG, "
        f"contract-meta, requirement, and jurisdiction logic live in the store extensions. Re-coupled: {domain}")
