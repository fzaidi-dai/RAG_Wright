"""EP-CORE-2 (ADR-0118): the capability implementation resolver -- the adapter-free heart of the ARD client.

A capability's manifest carries an `impl_ref` ("module:attr") pointing to its invoke factory, a
`(resources, inputs) -> result` callable (model factories ignore `resources`). `capability_impl(name)` reads the
light index, pulls the `impl_ref`, and imports it LAZILY -- so there is NO central engine-owned adapter dict, and a
developer registering a capability with an `impl_ref` makes it invocable with zero engine edits. ARD stays
metadata-only (ADR-0003): the registry holds a string pointer, never a callable; this module does the import.

Lives at the capabilities layer so BOTH the engine API invoker (`api/invoke.py`) and the ingestion pipeline's model
dispatch (`spans/model_capabilities.py`) resolve the same way."""
from __future__ import annotations

from importlib import import_module
from typing import Any, Callable


def capability_impl(name: str) -> Callable[..., Any]:
    """Resolve a capability name to its invoke factory via the manifest `impl_ref`. Raises `KeyError` if the name is
    unknown to the ARD catalog, `NotImplementedError` if it declares no `impl_ref` (not invokable by name)."""
    from rag_wright.capabilities.manifests import MANIFEST_SPECS

    spec = MANIFEST_SPECS.get(name)
    if spec is None:
        raise KeyError(f"unknown capability {name!r} (not in the ARD catalog)")
    ref = spec.impl_ref
    if not ref:
        raise NotImplementedError(f"capability {name!r} declares no impl_ref (not invokable by name)")
    module_path, _, attr = ref.partition(":")
    if not module_path or not attr:
        raise ValueError(f"capability {name!r} has a malformed impl_ref {ref!r} (expected 'module:attr')")
    return getattr(import_module(module_path), attr)
