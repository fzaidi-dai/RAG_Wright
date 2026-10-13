"""Where an agent's memory is written, and whose it is (T-5a.7a, AR-9, AR-10).

**The hazard this phase opened with, and the half T-5a.1 did not close.** `StoreBackend(namespace=None)`
does not fail — it falls back to a legacy path that returns `(assistant_id, "filesystem")` when an
assistant id is in the metadata and **`("filesystem",)` otherwise**. Read in the installed source, not
assumed. Both outcomes are forbidden here:

- `("filesystem",)` is **one namespace for every tenant**. Every customer's memory in one place, merged
  silently, with no error to notice.
- `(assistant_id, "filesystem")` is `AR-10`'s explicitly forbidden agent scope: shared across every user of
  the agent by construction, which is the shape LangChain's own docs warn about — *"if one user can write
  to memory that another user reads, a malicious user could inject instructions into shared state."*

So forgetting the factory is not a crash. It is a cross-tenant merge that looks like everything working.

**Four scopes, three writable** (`AR-9`), routed by path prefix:

| path | namespace | writable |
|---|---|---|
| `/memories/matter/` | `(customer, workspace, "memories")` | by the run |
| `/memories/firm/` | `(customer, "memories")` | yes — facts true of the customer |
| `/memories/product/` | shipped with the product | **no** (T-5a.7b) |
| anything else | ephemeral `StateBackend` | for the life of the run |

**The namespace factory REFUSES rather than falls back.** It reads the run's context off the
runtime -- whatever type the product supplies, read only through the `RunScope` protocol -- and if there is none it raises. That is deliberate defence in depth: T-5a.1 makes a context-free
invocation impossible at the door, and this makes it *fatal* if one ever arrives anyway — because the
alternative, silently, is one tenant reading another's memory at the moment a human approved something.
"""

from __future__ import annotations

from typing import Any

#: Path prefixes. **Trailing slashes matter**: `CompositeBackend` routes on the prefix, and `/memories/m`
#: would also match `/memories/matterhorn`.
SCOPE_ROOT = "/memories/scope/"
SHARED_ROOT = "/memories/shared/"
PRODUCT_ROOT = "/memories/product/"

#: The leaf every namespace ends with, so a store holding more than memory stays separable.
LEAF = "memories"


class MemoryWithoutTenancy(RuntimeError):
    """A memory operation reached the store with no run context.

    **Raised rather than defaulted, and that is the entire point of this module.** The fallback it
    replaces is silent and its consequence is a merge, not an error — so this must be loud enough that
    nobody is tempted to widen it into a default.
    """


def _context_of(runtime: Any) -> Any:
    """The run's context, or a refusal. Its type is the product's; only `scope` is read."""
    context = getattr(runtime, "context", None)
    if context is None:
        raise MemoryWithoutTenancy(
            "a memory operation reached the store with runtime.context=None. Every invocation must carry "
            "the context, RESUMES INCLUDED (AR-6) -- `harness/runs.py` is the only door precisely so this "
            "cannot happen. Falling back to a default namespace here would merge this run's memory with "
            "every other tenant's."
        )
    return context


def scope_namespace_of(runtime: Any) -> tuple[str, ...]:
    """The run's OWN memory: `scope + ("memories",)`.

    **Read off the run's `scope`, not off named ids** (engine ADR-0139 addendum). This was
    `(customer_id, workspace_id, LEAF)`, which is why the substrate's context type encoded this product's
    two-level tenancy. The narrowest part is in the key, so two runs of the same shared scope cannot see
    each other's notes -- the same isolation the document scope gives, applied to what the agent
    remembers.
    """
    from rag_wright.harness.scope import scope_namespace

    return scope_namespace(_context_of(runtime), LEAF)


def shared_namespace_of(runtime: Any) -> tuple[str, ...]:
    """The one WIDER memory a run may promote into: `shared_scope + ("memories",)`.

    **Deliberately wider than the run and deliberately not the root** (`AR-10`). A fact learned while
    working customer A's contracts is derived from customer A's documents; there is no scope above this one
    that anything may be promoted into by learning.

    **The substrate enforces that now, rather than trusting this function.** `validate_scope` refuses a
    `shared_scope` that is not a strict, non-empty prefix of `scope`, so sideways, the root and another
    tenant's chain are unrepresentable. A product with a one-part scope has none, and this mount is not
    offered to it at all.
    """
    from rag_wright.harness.scope import shared_namespace

    shared = shared_namespace(_context_of(runtime), LEAF)
    if shared is None:  # pragma: no cover - the mount is absent for such a product
        raise ValueError("this run has no shared scope; the shared mount should not have been routed")
    return shared


def build_memory_backend(store: Any) -> Any:
    """The routed backend: durable per-scope stores over ephemeral state.

    **`StateBackend` at the root is the default on purpose**, and the distinction is sharper than
    "ephemeral": its own docstring says files *"persist within a conversation thread but not across
    threads. State is automatically checkpointed."* So a scratch file survives a reload of the run that
    wrote it and is invisible to every other run — which is exactly `AR-9`'s **thread** scope, arrived at
    by using the default rather than by building anything.

    Durability **across** runs is the exception, granted per prefix, rather than the default that has to
    be taken away.

    **What "namespace" means here, since the agent sees a filesystem.** The model reads and writes paths;
    `StoreBackend` turns those into `BaseStore` operations keyed by a namespace TUPLE. The path is what
    the model thinks about, the namespace is what separates tenants, and they are different things on
    purpose: two matters write the same path and land under different namespaces.
    """
    from deepagents.backends import CompositeBackend, StateBackend, StoreBackend

    return CompositeBackend(
        default=StateBackend(),
        routes={
            SCOPE_ROOT: StoreBackend(store=store, namespace=scope_namespace_of),
            SHARED_ROOT: StoreBackend(store=store, namespace=shared_namespace_of),
            PRODUCT_ROOT: StoreBackend(store=store, namespace=product_namespace),
        },
    )


def product_namespace(_runtime: Any) -> tuple[str, ...]:
    """Product knowledge: **one namespace for everybody, deliberately** (T-5a.7b, `AR-9`).

    Everywhere else in this module a constant namespace is the bug. Here it is the design: product
    knowledge is **authored** and identical for every customer, so there is nothing to scope. What makes a
    shared namespace safe is the rule beside it — `MEMORY_PERMISSIONS` denies writes to this path, so
    nothing a run learns can ever land in it.

    **`AR-10` is the reason that pairing is not optional.** Product-level knowledge is authored, never
    learned and promoted; a writable shared scope is precisely how one customer's note becomes every
    customer's instruction.
    """
    from rag_wright.harness.scope import PRODUCT_NAMESPACE

    return (*PRODUCT_NAMESPACE, LEAF)


#: **Order is load-bearing: `_check_fs_permission` returns on the FIRST matching rule**, and its default
#: when nothing matches is `allow`. So the refusals come first, and a path with no rule is writable —
#: which is why the product deny is explicit rather than assumed from "nobody writes there".
MEMORY_PERMISSIONS = [
    # **Authored, never learned.** A write here would be a run teaching every customer something.
    {"operations": ["write"], "paths": [f"{PRODUCT_ROOT}**"], "mode": "deny"},
    # **Shared across a customer's matters, so a person approves** (`AR-11`). A fact learned in one matter
    # that becomes visible in every other is exactly the shape a persistence attack wants.
    {"operations": ["write"], "paths": [f"{SHARED_ROOT}**"], "mode": "interrupt"},
]


def memory_permissions() -> list[Any]:
    """The rules as the library wants them."""
    from deepagents import FilesystemPermission

    return [FilesystemPermission(**rule) for rule in MEMORY_PERMISSIONS]  # type: ignore[arg-type]




#: The paths `MemoryMiddleware` reads into the prompt each turn.
#:
#: Product scope is a SOURCE but not a writable one: it is read every turn and refused every write.
MEMORY_SOURCES = [SCOPE_ROOT, SHARED_ROOT, PRODUCT_ROOT]
