"""OKF conformance linter (FR-K.4, T46): parseable frontmatter, non-empty type, resolvable links.

Index and description quality is the retrieval ceiling of the whole embedding-free path, so the linter is a
quality gate, not a formality: it reports orphan rate, description coverage, and broken-link ratio as numbers,
not pass-or-fail alone. `passes` covers only the hard OKF v0.1 conformance rules (every concept has parseable
frontmatter with a non-empty `type`); the rates are quality signals the compile recipe is tuned against.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from rag_wright.okf.document import OkfParseError, parse_okf

_INDEX_NAME = "index.md"
_RESERVED = {"index.md", "log.md"}
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class LintReport(BaseModel):
    """Bundle conformance + quality numbers. `passes` is the hard OKF v0.1 conformance verdict."""

    total_concepts: int
    frontmatter_parseable: int
    type_non_empty: int
    total_index_entries: int
    description_coverage: float  # fraction of index entries carrying a description
    orphan_rate: float  # fraction of concept files not linked from any index.md
    broken_link_ratio: float  # fraction of intra-bundle links whose target file is absent
    passes: bool


def _concept_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.md") if p.name not in _RESERVED]


def _resolve(link: str, source: Path, root: Path) -> Path | None:
    """Resolve an intra-bundle markdown link to a path, or None if it is external (a URL / anchor)."""
    target = link.split("#", 1)[0].strip()
    if not target or "://" in target:
        return None
    if target.startswith("/"):
        return root / target.lstrip("/")
    return (source.parent / target).resolve()


def lint_bundle(root: Path) -> LintReport:
    """Lint an OKF bundle, returning conformance + quality numbers."""
    root = Path(root)
    concepts = _concept_files(root)

    parseable = type_ok = 0
    for path in concepts:
        try:
            fm, _ = parse_okf(path.read_text(encoding="utf-8"))
        except OkfParseError:
            continue
        parseable += 1
        if str(fm.get("type") or "").strip():
            type_ok += 1

    # index entries: every `* [title](link) - desc` line across all index.md files
    entries_total = entries_with_desc = 0
    links_total = links_broken = 0
    linked_targets: set[Path] = set()
    for index_path in root.rglob(_INDEX_NAME):
        content = index_path.read_text(encoding="utf-8")
        for raw in content.splitlines():
            line = raw.strip()
            if not line.startswith("*"):
                continue
            m = _LINK.search(line)
            if not m:
                continue
            entries_total += 1
            if " - " in line.split(")", 1)[-1]:
                entries_with_desc += 1
        for m in _LINK.finditer(content):
            resolved = _resolve(m.group(1), index_path, root)
            if resolved is None:
                continue
            links_total += 1
            if resolved.exists():
                linked_targets.add(resolved.resolve())
            else:
                links_broken += 1

    orphans = sum(1 for c in concepts if c.resolve() not in linked_targets)
    n = len(concepts) or 1
    return LintReport(
        total_concepts=len(concepts),
        frontmatter_parseable=parseable,
        type_non_empty=type_ok,
        total_index_entries=entries_total,
        description_coverage=(entries_with_desc / entries_total) if entries_total else 1.0,
        orphan_rate=orphans / n,
        broken_link_ratio=(links_broken / links_total) if links_total else 0.0,
        passes=parseable == len(concepts) and type_ok == len(concepts),
    )
