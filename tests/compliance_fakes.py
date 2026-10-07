"""ING-8e: a hermetic stand-in for the slice of the GENERIC store seam that `ComplianceStore` reads and writes
(`kg_read` / `kg_write` / `type_names` / `schema_packs` / `ensure_pack_schema`). The Requirement reads moved off the
generic store onto the compliance pack's `ComplianceStore`, so compliance tests fake the seam, not the facade."""
from __future__ import annotations


class FakeRequirementSeam:
    """Requirement rows behind the generic seam. `kg_read` honors `where` (scalar = equality, list = IN, an empty list
    = scope-to-nothing) and `distinct`; every call is recorded in `reads` so a test can assert what was queried."""

    def __init__(self, rows=()) -> None:
        self.rows = [dict(r) for r in rows]
        self.reads: list[dict] = []
        self.packs: list[str] = []

    def schema_packs(self) -> list[str]:
        return list(self.packs)

    def ensure_pack_schema(self, ttl: str) -> None:
        self.packs.append(ttl)

    def type_names(self) -> set[str]:
        return {"Requirement"}

    def kg_read(self, node_type, *, where=None, fields=None, distinct=None, order_by=None, limit=None):
        self.reads.append({"type": node_type, "where": where, "distinct": distinct})
        rows = self.rows
        for key, value in (where or {}).items():
            if isinstance(value, list):
                rows = [r for r in rows if r.get(key) in set(value)]
            else:
                rows = [r for r in rows if r.get(key) == value]
        if distinct:
            return [{distinct: v} for v in dict.fromkeys(r.get(distinct) for r in rows)]
        return [{f: r.get(f) for f in fields} for r in rows] if fields else [dict(r) for r in rows]

    def kg_write(self, nodes, edges=()) -> None:
        self.rows.extend(dict(n.props) for n in nodes)
