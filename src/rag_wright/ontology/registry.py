"""The entity registry (T8, FR-C.8 / FR-C.7).

Built from the EDGAR `company_tickers.json` seed (T7) with CIKs as the canonical `entity_id`s. CIK
normalization **reuses `corpus.edgar.normalize_cik`**, the single canonical CIK -> `EntityId` point,
so the registry cannot drift from the T1 identifier contract: a messy EDGAR CIK form (integer,
unpadded, `CIK`-prefixed) lands on the same canonical `EntityId`, which is what stops the graph from
fragmenting across surface forms.

Lookup is **closed-world**: `resolve` returns `None` for an unknown surface form, never a fabricated
id. The concrete matching strategy (fuzzy / embedding / language-model-assisted, SPEC section 16.3)
is T24's; this registry is the closed set T24 resolves against, plus an exact normalized-surface-form
index.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from rag_wright.contracts.identifiers import EntityId
from rag_wright.corpus.edgar import normalize_cik, normalize_name


class RegistryRecord(BaseModel):
    """One registered entity: its canonical id, conformed name, ticker, and known aliases."""

    entity_id: EntityId
    canonical_name: str
    ticker: Optional[str] = None
    aliases: list[str] = []


class EntityRegistry:
    """A closed-world registry of canonical entities, indexed by normalized surface form."""

    def __init__(self) -> None:
        self._by_id: dict[str, RegistryRecord] = {}
        self._index: dict[str, EntityId] = {}  # normalized surface form -> entity_id
        self.skipped_ciks: list[str] = []  # raw CIK values that failed normalization

    def _index_surface(self, surface: str, entity_id: EntityId) -> None:
        key = normalize_name(surface)
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
        return self._index.get(normalize_name(surface_form))

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, entity_id: EntityId) -> bool:
        return entity_id.value in self._by_id

    @classmethod
    def from_company_tickers(
        cls,
        rows: list[dict],
        *,
        aliases_by_cik: Optional[dict[str, list[str]]] = None,
    ) -> EntityRegistry:
        """Build the registry from `company_tickers.json` rows, normalizing each CIK to an `EntityId`.

        `aliases_by_cik` maps a canonical `EntityId` value to former names / ticker aliases (from the
        EDGAR submissions data, T7). A row whose CIK is genuinely invalid is skipped and recorded in
        `skipped_ciks`, never fabricated into the registry.
        """
        aliases_by_cik = aliases_by_cik or {}
        registry = cls()
        for row in rows:
            raw_cik = row["cik_str"]
            try:
                entity_id = normalize_cik(raw_cik)
            except ValueError:
                registry.skipped_ciks.append(str(raw_cik))
                continue
            registry.add(
                RegistryRecord(
                    entity_id=entity_id,
                    canonical_name=row["title"],
                    ticker=row.get("ticker"),
                    aliases=aliases_by_cik.get(entity_id.value, []),
                )
            )
        return registry
