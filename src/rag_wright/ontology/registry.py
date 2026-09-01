"""The entity registry (T8, FR-C.8 / FR-C.7) -- a DOMAIN-NEUTRAL closed-world registry of canonical entities.

ADR-0067: the registry is generic. A domain's canonical `entity_id`s and their surface normalization are the
domain's concern: the surface-form normalizer is INJECTABLE (`EntityRegistry(normalize=...)`, default = a generic
name key), and the domain's own builder constructs the registry (the reference builder lives in the corpus
layer, keyed by that corpus's canonical ids). This module imports nothing corpus-specific.

Lookup is **closed-world**: `resolve` returns `None` for an unknown surface form, never a fabricated id. The
concrete matching strategy (fuzzy / embedding / language-model-assisted, SPEC section 16.3) is T24's; this
registry is the closed set T24 resolves against, plus an exact normalized-surface-form index.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from pydantic import BaseModel

from rag_wright.contracts.identifiers import EntityId

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def default_surface_key(name: str) -> str:
    """The default DOMAIN-NEUTRAL surface-form key: lowercase, non-alphanumeric runs folded to a single space."""
    return _NON_ALNUM.sub(" ", (name or "").lower()).strip()


class RegistryRecord(BaseModel):
    """One registered entity: its canonical id, conformed name, ticker, and known aliases."""

    entity_id: EntityId
    canonical_name: str
    ticker: Optional[str] = None
    aliases: list[str] = []


class EntityRegistry:
    """A closed-world registry of canonical entities, indexed by normalized surface form."""

    def __init__(self, *, normalize: Optional[Callable[[str], str]] = None) -> None:
        self._by_id: dict[str, RegistryRecord] = {}
        self._index: dict[str, EntityId] = {}  # normalized surface form -> entity_id
        self.skipped_ids: list[str] = []  # raw canonical-id values that failed normalization (builder-specific)
        self._normalize = normalize or default_surface_key  # ADR-0067: domain-neutral, injectable

    def _index_surface(self, surface: str, entity_id: EntityId) -> None:
        key = self._normalize(surface)
        if key:
            self._index.setdefault(key, entity_id)

    def add(self, record: RegistryRecord) -> None:
        self._by_id[record.entity_id.value] = record
        self._index_surface(record.canonical_name, record.entity_id)
        if record.ticker:
            self._index_surface(record.ticker, record.entity_id)
        for alias in record.aliases:
            self._index_surface(alias, record.entity_id)

    def get(self, entity_id: EntityId) -> Optional[RegistryRecord]:
        return self._by_id.get(entity_id.value)

    def resolve(self, surface_form: str) -> Optional[EntityId]:
        """The canonical `entity_id` for a known surface form, or `None` (closed-world)."""
        return self._index.get(self._normalize(surface_form))

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, entity_id: EntityId) -> bool:
        return entity_id.value in self._by_id
