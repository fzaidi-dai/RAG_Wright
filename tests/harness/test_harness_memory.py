"""Where an agent's memory is written, and whose it is.

Ported from RuleWright under ADR-0139's handover (PS-48f); the substrate is `rag_wright.harness.memory`, driven with the
fabric product's three-part scope. RuleWright's tests of its own memory prompt (that its notes are framed as notes,
not instructions) stay in RuleWright: the framing is the product's text.

**The fallback this module exists to refuse.** `StoreBackend(namespace=None)` does not fail -- it returns
`(assistant_id, "filesystem")` when an assistant id is in the metadata, and `("filesystem",)` otherwise. The first is
an agent-wide scope shared across every user; the second is **one namespace for every scope**. So the failure mode
of forgetting is not an exception but a cross-scope merge that looks like everything working -- which is why the
guard below reads the source, and why the namespace factory raises instead of defaulting.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_wright.harness.memory import (
    MEMORY_SOURCES,
    PRODUCT_ROOT,
    SCOPE_ROOT,
    SHARED_ROOT,
    MemoryWithoutTenancy,
    build_memory_backend,
    scope_namespace_of,
    shared_namespace_of,
)
from tests.harness.fabric import FabricContext

#: The whole engine, not only the harness: a store backend built anywhere without a namespace merges scopes.
ENGINE = Path(__file__).resolve().parents[2] / "src" / "rag_wright"


def runtime(context: FabricContext | None = None) -> SimpleNamespace:
    return SimpleNamespace(context=context or FabricContext(account="acme-mills", season="ss26", collection="resort"))


def test_A_COLLECTIONS_MEMORY_IS_KEYED_BY_ITS_WHOLE_SCOPE() -> None:
    """Two collections of the same account must not see each other's notes -- the same isolation the data has,
    applied to what the agent remembers."""
    assert scope_namespace_of(runtime()) == ("acme-mills", "ss26", "resort", "memories")


def test_ANOTHER_COLLECTION_OF_THE_SAME_ACCOUNT_GETS_A_DIFFERENT_NAMESPACE() -> None:
    other = FabricContext(account="acme-mills", season="ss26", collection="outerwear")

    assert scope_namespace_of(runtime()) != scope_namespace_of(runtime(other))


def test_THE_SHARED_MEMORY_IS_THE_PRODUCTS_DECLARED_ANCESTOR_AND_NO_WIDER() -> None:
    """A fact learned while working one account's data is derived from that account's data. There is no scope
    above the one the product declared that learning may promote into."""
    assert shared_namespace_of(runtime()) == ("acme-mills", "ss26", "memories")

    elsewhere = FabricContext(account="another-mill", season="ss26", collection="resort")
    assert shared_namespace_of(runtime()) != shared_namespace_of(runtime(elsewhere))


def test_A_RUN_WITH_NO_CONTEXT_IS_REFUSED_RATHER_THAN_DEFAULTED() -> None:
    """**The whole module, in one assertion.** A forgetful resume yields `runtime.context is None`; the entry points
    make that impossible, and this makes it fatal if it happens anyway, because the alternative is silently merging
    this run's memory into every scope's."""
    for namespace in (scope_namespace_of, shared_namespace_of):
        with pytest.raises(MemoryWithoutTenancy, match="runtime.context=None"):
            namespace(SimpleNamespace(context=None))


def test_A_RUNTIME_WITH_NO_CONTEXT_ATTRIBUTE_IS_REFUSED_TOO() -> None:
    """Absent and `None` must fail the same way, or the difference becomes the gap."""
    with pytest.raises(MemoryWithoutTenancy):
        scope_namespace_of(SimpleNamespace())


# --- the guard ----------------------------------------------------------------------------------------


def _store_backends_without_a_namespace(source: str, filename: str = "<planted>") -> list[str]:
    """Every `StoreBackend(...)` construction that does not pass `namespace=`."""
    offenders = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "StoreBackend" and not any(keyword.arg == "namespace" for keyword in node.keywords):
            offenders.append(ast.unparse(node)[:90])
    return offenders


def test_NO_STORE_BACKEND_IS_CONSTRUCTED_WITHOUT_A_NAMESPACE_FACTORY() -> None:
    """**The guard the module exists for.** `namespace` is optional in the library and its default is shared, so
    nothing about a working system reveals a missing one. It reads the source because the behaviour looks identical
    until two scopes are in it -- and it asserts it read something, because a guard over a moved folder passes on
    nothing (the copy this was ported from had been scanning a folder that no longer existed)."""
    modules = [m for m in sorted(ENGINE.rglob("*.py")) if "__pycache__" not in m.parts]
    assert any(m.name == "memory.py" for m in modules), f"the guard is not reading the engine's source: {ENGINE}"

    offenders = {m.relative_to(ENGINE).as_posix(): found for m in modules
                 if (found := _store_backends_without_a_namespace(m.read_text(encoding="utf-8"), str(m)))}

    assert not offenders, (
        f"these construct a StoreBackend with no namespace factory: {offenders}. The library's default is ONE "
        "namespace for every scope, or one per assistant; neither fails, both merge."
    )


def test_THE_GUARD_CATCHES_A_PLANTED_CONSTRUCTION() -> None:
    """Planted, because the real tree complies and would prove nothing about the detector."""
    assert _store_backends_without_a_namespace("b = StoreBackend(store=s)\n") == ["StoreBackend(store=s)"]
    assert _store_backends_without_a_namespace("b = StoreBackend(store=s, namespace=scope_namespace_of)\n") == []


def test_THE_GUARD_SEES_AN_ATTRIBUTE_CONSTRUCTION_TOO() -> None:
    """`backends.StoreBackend(...)` is the same mistake spelled differently."""
    assert _store_backends_without_a_namespace("b = backends.StoreBackend(store=s)\n")


# --- the routes ---------------------------------------------------------------------------------------


def test_THE_BACKEND_ROUTES_THE_DURABLE_SCOPES_AND_DEFAULTS_TO_EPHEMERAL() -> None:
    """**Durability is the exception, granted per prefix.** Anything written outside a memory path lives for the run
    and no longer; a durable default would make every stray write permanent and scope-keyed forever."""
    backend = build_memory_backend(store=object())

    assert set(backend.routes) == {SCOPE_ROOT, SHARED_ROOT, PRODUCT_ROOT}
    assert type(backend.default).__name__ == "StateBackend"


def test_THE_PATHS_END_IN_SLASHES() -> None:
    """`CompositeBackend` routes on the prefix, so `/memories/scope` would also capture `/memories/scopehorn`."""
    for root in (SCOPE_ROOT, SHARED_ROOT, PRODUCT_ROOT):
        assert root.endswith("/")


def _mode_for(operation: str, path: str) -> str:
    """Ask the LIBRARY how it resolves the harness's rules, rather than restating them. **A guarded private import**:
    `_check_fs_permission` is what the filesystem middleware actually uses; if it moves, this fails loudly."""
    from deepagents.middleware.filesystem import _check_fs_permission

    from rag_wright.harness.memory import memory_permissions

    return _check_fs_permission(memory_permissions(), operation, path)


def test_PRODUCT_SCOPE_IS_READ_BUT_NEVER_WRITTEN() -> None:
    """**Read every turn, refused every write** -- the pairing that makes a shared namespace safe."""
    assert PRODUCT_ROOT in MEMORY_SOURCES
    assert _mode_for("write", f"{PRODUCT_ROOT}anything.md") == "deny"
    assert _mode_for("read", f"{PRODUCT_ROOT}anything.md") == "allow"


def test_THE_PRODUCT_NAMESPACE_IS_SHARED_AND_THAT_IS_THE_DESIGN() -> None:
    """**Everywhere else a constant namespace is the bug.** Here it is correct: product knowledge is authored and
    identical for every scope, and what makes it safe is the deny rule beside it, not the namespace."""
    from rag_wright.harness.memory import product_namespace

    assert product_namespace(runtime()) == product_namespace(SimpleNamespace(context=None))
    assert product_namespace(runtime()) == ("product", "memories")


def test_THE_LIBRARY_RESOLVES_ON_FIRST_MATCH_AND_DEFAULTS_TO_ALLOW() -> None:
    """**Two library facts the rule ORDER depends on**, pinned so a change is visible: first match wins, so a broad
    allow before a narrow deny defeats it; and an unmatched path is allowed, which is why the product deny is
    explicit."""
    from deepagents import FilesystemPermission
    from deepagents.middleware.filesystem import _check_fs_permission

    first_wins = [FilesystemPermission(operations=["write"], paths=["/x/**"], mode="allow"),
                  FilesystemPermission(operations=["write"], paths=["/x/**"], mode="deny")]
    assert _check_fs_permission(first_wins, "write", "/x/a.md") == "allow", "the library no longer resolves on first match"
    assert _check_fs_permission([], "write", "/anywhere.md") == "allow", "an unmatched path is no longer allowed"


def test_A_WRITE_TO_THE_SHARED_SCOPE_IS_GATED_RATHER_THAN_ALLOWED() -> None:
    """A fact learned in one collection that becomes visible in every other is exactly the shape a persistence attack
    wants, so a person approves it."""
    assert _mode_for("write", f"{SHARED_ROOT}note.md") == "interrupt"


def test_A_WRITE_TO_THIS_SCOPE_NEEDS_NO_APPROVAL() -> None:
    """Gating the ordinary case would train people to approve without reading, which is worse than not gating."""
    assert _mode_for("write", f"{SCOPE_ROOT}note.md") == "allow"


def test_THE_REFUSALS_COME_FIRST_IN_THE_RULE_LIST() -> None:
    """First match wins, so an allow ahead of a deny silently defeats it; asserted on the list itself."""
    from rag_wright.harness.memory import MEMORY_PERMISSIONS

    assert "allow" not in [rule["mode"] for rule in MEMORY_PERMISSIONS], (
        "an `allow` rule is listed; under first-match it can only shadow a later refusal"
    )
