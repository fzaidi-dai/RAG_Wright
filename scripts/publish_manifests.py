"""Publish every capability's ARD manifest into the shared registry root.

Writes one `<slug>.json` per capability spec (capabilities/manifests.py) into the root resolved from
`ARD_REGISTRY_ROOT` (default `~/.air/registry`), where GraphWright's RegistryStore discovers them.
Run it after adding or changing a capability's manifest spec; the root is regenerable from the
committed specs, so it is safe to re-run. A live RegistryStore load is a GraphWright-side step.

    uv run python scripts/publish_manifests.py
"""

from __future__ import annotations

from rag_wright.capabilities.ard import registry_root
from rag_wright.capabilities.manifests import MANIFEST_SPECS, publish_all


def main() -> None:
    root = registry_root()
    paths = publish_all(root=root)
    print(f"published {len(paths)} manifest(s) to {root}:")
    for path in sorted(paths):
        print(f"  {path.name}")
    print(f"specs: {sorted(MANIFEST_SPECS)}")


if __name__ == "__main__":
    main()
