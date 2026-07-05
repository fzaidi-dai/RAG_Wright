"""Ontology derivation (T8, FR-C.8): reconcile the T4 ontology against the real CUAD data.

T4 declared the 41 `ClauseCategory` values from the published CUAD label set. This module confirms
they match the actual `master_clauses.csv` columns and produces a CSV-column -> canonical-category
mapping. The CSV headers carry artifacts the reconciliation absorbs so downstream annotation reading
(T9) binds to the canonical categories regardless: paired answer columns use inconsistent spacing
(`-Answer` and `- Answer`), and some names are cased differently (`Ip Ownership Assignment` vs the
ontology's canonical `IP Ownership Assignment`). Matching is spacing-tolerant on answer columns and
case-insensitive on category names; the ontology keeps its canonical casing.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from rag_wright.contracts.ontology import ClauseCategory

_ANSWER_SUFFIX = re.compile(r"-\s*answer$", re.IGNORECASE)


def clause_category_columns(header: list[str]) -> list[str]:
    """The category columns of `master_clauses.csv`: everything but `Filename` and answer columns."""
    return [
        col
        for col in header
        if col.strip().lower() != "filename" and not _ANSWER_SUFFIX.search(col.strip())
    ]


class Reconciliation(BaseModel):
    """The result of reconciling CSV category columns against the `ClauseCategory` ontology."""

    matched: dict[str, ClauseCategory]  # CSV column -> canonical category
    missing: list[ClauseCategory]  # ontology categories with no CSV column
    extra: list[str]  # CSV columns matching no ontology category

    @property
    def ok(self) -> bool:
        return not self.missing and not self.extra


def reconcile_clause_categories(csv_columns: list[str]) -> Reconciliation:
    """Match CSV category columns to `ClauseCategory` (case-insensitive), reporting gaps both ways."""
    by_norm = {category.value.lower(): category for category in ClauseCategory}
    matched: dict[str, ClauseCategory] = {}
    extra: list[str] = []
    seen: set[ClauseCategory] = set()
    for column in csv_columns:
        category = by_norm.get(column.strip().lower())
        if category is None:
            extra.append(column)
        else:
            matched[column] = category
            seen.add(category)
    missing = [category for category in ClauseCategory if category not in seen]
    return Reconciliation(matched=matched, missing=missing, extra=extra)
