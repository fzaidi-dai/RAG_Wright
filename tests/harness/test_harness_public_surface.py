"""A product builds, runs and checks its agent from `rag_wright.harness` alone (PS-48i).

**`__all__` is the public surface, and a product reaching past it is building on what the engine may change
without notice.** The first product reached into eleven submodules for names it needed -- the run's decision
ledger, the dispatch bound, `build_worker`, `run_key` among them -- because they were never exported. So the names a
product's code needs are exported, and three guards keep it that way: the fabric product (the engine's stand-in for
a greenfield product) imports nothing from a harness submodule, no engine-facing doc shows a submodule import, and
the names the first product's own code uses stay on the surface.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import rag_wright.harness as harness
from tests.arch.test_doc_references import _DOCS

ROOT = Path(__file__).resolve().parents[2]
FABRIC = Path(__file__).with_name("fabric.py")

#: The harness names the first product's own code (its agent, its evals and its scripts) imports. Each was reached
#: through a submodule until PS-48i; a name dropping off the surface would put that product back on internals.
WHAT_A_PRODUCTS_CODE_USES = {
    "WhatTheRunDecided", "RecordTheDecisions", "DISPATCHES_PER_RUN", "ConfirmTheView", "bridged_tools", "js_name",
    "scope_namespace_of", "shared_namespace_of", "rule_named", "run_key", "chat_manager_tools", "harness_tracing_on",
    "withhold", "build_worker", "guessed_identifiers", "RecordTheContext", "Fault", "InjectFaults",
    "what_will_run", "only_the_chat_manager", "stub_orchestrator",
    # a product's guards on its own tier split
    "direct_names", "orchestrator_direct", "worker_operations",
}

_SUBMODULE_IMPORT = re.compile(r"^\s*(?:from\s+rag_wright\.harness\.\w+\s+import|import\s+rag_wright\.harness\.\w+)",
                               re.M)


def _submodule_imports(source: str) -> list[str]:
    """Every import of a harness SUBMODULE in Python source, as `module.name`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("rag_wright.harness."):
            found += [f"{node.module}.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if alias.name.startswith("rag_wright.harness.")]
    return found


def test_THE_FABRIC_PRODUCT_IS_BUILT_FROM_THE_PUBLIC_SURFACE_ALONE() -> None:
    """**The stand-in for a greenfield product.** What it needs, a real product needs, so it imports only from
    `rag_wright.harness` -- and it asserts it imports from there at all, or the guard reads an empty file."""
    source = FABRIC.read_text(encoding="utf-8")

    assert "from rag_wright.harness import" in source, "the fabric product imports nothing from the harness"
    assert not _submodule_imports(source), f"the fabric product reaches past `__all__`: {_submodule_imports(source)}"


def test_NO_ENGINE_FACING_DOC_SHOWS_A_SUBMODULE_IMPORT() -> None:
    """A doc that teaches `from rag_wright.harness.tiers import ...` teaches every reader to build on internals."""
    assert _DOCS, "the doc set is empty; this guard is checking nothing"
    offenders = [doc.relative_to(ROOT).as_posix() for doc in _DOCS
                 if doc.is_file() and _SUBMODULE_IMPORT.search(doc.read_text(encoding="utf-8"))]

    assert not offenders, f"these docs show a harness submodule import: {offenders}"


def test_WHAT_A_PRODUCTS_CODE_USES_IS_EXPORTED() -> None:
    missing = WHAT_A_PRODUCTS_CODE_USES - set(harness.__all__)

    assert not missing, f"a product would have to reach into a submodule for {sorted(missing)}"


def test_EVERY_EXPORTED_NAME_RESOLVES_ONCE() -> None:
    """A name in `__all__` that does not import is a broken promise; one listed twice hides a removal."""
    assert len(harness.__all__) == len(set(harness.__all__))
    for name in harness.__all__:
        assert hasattr(harness, name), f"`{name}` is in __all__ and does not resolve"


def test_THE_GUARDS_CATCH_A_PLANTED_SUBMODULE_IMPORT() -> None:
    """Planted, because the real files comply and would prove nothing about the detectors."""
    assert _submodule_imports("from rag_wright.harness.tiers import direct_names\n") == ["rag_wright.harness.tiers.direct_names"]
    assert _submodule_imports("import rag_wright.harness.seen\n") == ["rag_wright.harness.seen"]
    assert _submodule_imports("from rag_wright.harness import run_key\n") == []
    assert _SUBMODULE_IMPORT.search("```python\nfrom rag_wright.harness.tiers import x\n```")
    assert not _SUBMODULE_IMPORT.search("```python\nfrom rag_wright.harness import x\n```")
