"""What a run is scoped to, as the substrate sees it (engine ADR-0139 addendum, T-7.58).

**The substrate asks for a tuple of strings and nothing else.** It used to read `context.customer_id` and
`context.workspace_id` -- so `AgentContext`, the type every agent receives as `context_schema`, encoded
this product's two-level tenancy, and a product with one level or three could not fill it. The engine's
decision (option (c), after our finding) is a `runtime_checkable` protocol whose members are a scope and
an optional wider scope a run may promote into. The product keeps its own context type, its typed ids and
its `for_tenant` rule; it adds these two.

**The three rules below are the substrate's to ENFORCE, not the product's to honour**, which is the whole
difference between `AR-10` as a guarantee and `AR-10` as a convention:

1. `scope` is required and non-empty, and no part may contain the key separator -- otherwise two different
   scopes can produce one key.
2. `shared_scope` is optional, and must be a **strict, non-empty prefix** of `scope`. That makes a
   sideways scope, the root, and anything outside the run's own chain unrepresentable rather than merely
   discouraged. A one-part scope has no shared scope at all, which is the honest answer when there is
   nothing above the leaf.
3. The authored, everyone-wide namespace is the harness's own constant and is read-only by construction.
   A writable scope shared by every tenant cannot be configured, so *"a writable shared scope is precisely
   how one customer's note becomes every customer's instruction"* stops being a permission rule somebody
   has to remember to write.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

#: What separates the parts of a composed key. A part containing it could make two scopes collide.
KEY_SEPARATOR = ":"

#: The authored namespace every tenant reads and none writes. **The harness owns this value** -- a product
#: supplies the contents, never the name, because a product-chosen name is one a second product could set
#: to its own scope.
PRODUCT_NAMESPACE: tuple[str, ...] = ("product",)


@runtime_checkable
class RunScope(Protocol):
    """The only thing the substrate needs to know about a run's tenancy.

    A product's own context type satisfies this by having the two members; it keeps whatever else it wants
    (`AgentContext` keeps typed ids, a user id and `for_tenant`).
    """

    #: Narrowest first: `("acme", "matter-7")`. Required, non-empty.
    scope: tuple[str, ...]
    #: The one wider scope a run may promote a learned fact into, or `None` when there is none.
    shared_scope: tuple[str, ...] | None


def validate_scope(context: Any) -> None:
    """Refuse a scope the substrate cannot key or bound. Raises `ValueError`.

    Called where a run begins rather than where a namespace is built: a bad scope should fail the run, not
    the first memory write, which is far from the cause.
    """
    scope = getattr(context, "scope", None)
    if not isinstance(scope, tuple) or not scope or not all(isinstance(p, str) and p for p in scope):
        raise ValueError(
            f"a run's `scope` must be a non-empty tuple of non-empty strings; got {scope!r}. The substrate "
            "keys memory and the run's thread from it, so an empty scope would share one namespace with "
            "every other run."
        )
    bad = [part for part in scope if KEY_SEPARATOR in part]
    if bad:
        raise ValueError(
            f"these scope parts contain the key separator {KEY_SEPARATOR!r}: {bad}. Two different scopes "
            "could then compose the same key, which is a cross-tenant read wearing a formatting bug."
        )

    shared = getattr(context, "shared_scope", None)
    if shared is None:
        return
    if not isinstance(shared, tuple) or not shared:
        raise ValueError(
            f"`shared_scope` must be a non-empty tuple or None; got {shared!r}. `None` is how a scope with "
            "nothing above it says so."
        )
    if shared == scope or tuple(scope[:len(shared)]) != shared:
        raise ValueError(
            f"`shared_scope` {shared!r} is not a STRICT prefix of `scope` {scope!r}. A run may only promote "
            "into its own ancestors: anything else is sideways, the root, or another tenant's chain."
        )


def scope_namespace(context: Any, leaf: str) -> tuple[str, ...]:
    """The run's own writable memory: `scope + (leaf,)`."""
    return (*context.scope, leaf)


def shared_namespace(context: Any, leaf: str) -> tuple[str, ...] | None:
    """The one wider namespace a run may promote into, or `None` when it has none."""
    shared = getattr(context, "shared_scope", None)
    return None if shared is None else (*shared, leaf)


def thread_key(context: Any, run_id: str) -> str:
    """The run's thread id: its scope and its run id, composed.

    **Not the run id alone.** The run id is the PUBLIC identity a client keeps; the thread is ours and
    never leaves the process, and keying it by scope is what makes a guessed run id from another tenant
    resolve to nothing by construction.
    """
    return KEY_SEPARATOR.join((*context.scope, run_id))

#: The two members the substrate needs. Named once so the type check and the value check cannot disagree.
REQUIRED_MEMBERS = ("scope",)


def carries_scope(schema: Any) -> bool:
    """Whether a context TYPE can carry a scope, checked before any run begins.

    **`issubclass` is unavailable here and that is a language fact, not an oversight**: a
    `runtime_checkable` Protocol with data members supports `isinstance` and raises `TypeError` on
    `issubclass`. So the class surface is inspected directly -- a property or class attribute shows up via
    `hasattr`, while a bare dataclass annotation exists only on instances and has to be read from
    `__annotations__` / `__dataclass_fields__`. A product may legitimately do either, so both count.

    `RunScope` remains the documented contract and `isinstance` still works on the instance; this is the
    type-level half, for the one check that happens before there is an instance to look at.
    """
    if not isinstance(schema, type):
        return False
    declared = set(getattr(schema, "__dataclass_fields__", {}) or {})
    for base in getattr(schema, "__mro__", (schema,)):
        declared |= set(getattr(base, "__annotations__", {}) or {})
    return all(hasattr(schema, name) or name in declared for name in REQUIRED_MEMBERS)
