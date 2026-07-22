"""OKF bundle compile (FR-K.1/K.4, T46): deterministic bundle write from sidecar text + enrichment.

Given the chunk texts (from the T40 sidecar) and the per-clause enrichment (category + description), write
an OKF v0.1 conformant bundle: one markdown file per clause (non-empty `type` frontmatter, body byte-faithful
to the sidecar text), organized `root -> <category>/ -> <clause>.md`, with an `index.md` per directory and the
bundle root stamped with `okf_version` and the compile-recipe version. No re-chunk; `chunk_id` is unchanged.

Deterministic (no model call) and content-hash gated: recompiling an unchanged corpus under the same recipe
does effectively no work. `recompile_category` rewrites one subtree (T51's fast-iteration path).

Registered under the canonical slug `okf_compile` (category 3, a foundation derivation: internal registry
entry, no ARD manifest -- same shape as `ontology_registry_derivation`).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.okf.document import parse_okf, serialize_okf
from rag_wright.okf.enrich import EnrichedClause

OKF_VERSION = "0.1"
RECIPE_VERSION = "okf-acord-v1"
CLAUSE_TYPE = "Clause"
UNCATEGORIZED = "_uncategorized"
_INDEX = "index.md"
_MANIFEST = ".okf_manifest.json"


class OkfBundleManifest(BaseModel):
    """The compile result and the `okf_compile` capability contract: what the bundle root records."""

    okf_version: str
    recipe_version: str
    n_clauses: int
    n_categorized: int
    categories: dict[str, int]  # category label -> clause-file count (UNCATEGORIZED for abstentions)
    content_hash: str


def _slug(label: str) -> str:
    """Filesystem-safe directory slug for a category label (`IP Ownership/License` -> `ip-ownership-license`)."""
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s or UNCATEGORIZED


def _source_doc_id(chunk_id: str) -> str:
    return chunk_id.rsplit(":", 2)[0]  # <source_doc_id>:<chunk_index>:<content_hash>


def _dir_for(e: EnrichedClause) -> str:
    return _slug(e.category) if e.categorized else UNCATEGORIZED


def _concept_document(chunk_id: str, text: str, e: EnrichedClause) -> str:
    frontmatter = {
        "type": CLAUSE_TYPE,
        "title": _source_doc_id(chunk_id),
        "description": e.description,
        "tags": [e.category] if e.categorized else [],
        "category": e.category if e.categorized else UNCATEGORIZED,
        "chunk_id": chunk_id,
        "source_doc_id": _source_doc_id(chunk_id),
    }
    return serialize_okf(frontmatter, text)


def _bundle_hash(
    texts: dict[str, str], enrichment: dict[str, EnrichedClause], recipe_version: str
) -> str:
    """A content hash over (chunk_id, category, description) plus the recipe version. chunk_id already
    embeds the text content hash, so an unchanged clause + unchanged enrichment + unchanged recipe hashes
    the same, and the compile gate skips."""
    h = hashlib.sha256()
    h.update(recipe_version.encode("utf-8"))
    for cid in sorted(texts):
        e = enrichment[cid]
        h.update(f"\x00{cid}\x00{e.categorized}\x00{e.category}\x00{e.description}".encode("utf-8"))
    return h.hexdigest()


def _index_text(entries: list[tuple[str, str, str]], *, heading: str) -> str:
    """Render an index.md section: `* [title](link) - description`, sorted by title."""
    lines = [f"# {heading}", ""]
    for title, link, desc in sorted(entries, key=lambda e: e[0].lower()):
        suffix = f" - {desc}" if desc else ""
        lines.append(f"* [{title}]({link}){suffix}")
    return "\n".join(lines) + "\n"


def _write_category_index(category_dir: Path) -> None:
    entries: list[tuple[str, str, str]] = []
    for md in sorted(category_dir.glob("*.md")):
        if md.name == _INDEX:
            continue
        fm, _ = parse_okf(md.read_text(encoding="utf-8"))
        entries.append((str(fm.get("title") or md.stem), md.name, str(fm.get("description") or "")))
    if entries:
        (category_dir / _INDEX).write_text(_index_text(entries, heading="Clauses"), encoding="utf-8")


def _write_root_index(root: Path, *, okf_version: str, recipe_version: str) -> None:
    entries: list[tuple[str, str, str]] = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        n = len([m for m in sub.glob("*.md") if m.name != _INDEX])
        entries.append((sub.name, f"{sub.name}/{_INDEX}", f"{n} clauses"))
    frontmatter = {"okf_version": okf_version, "compile_recipe_version": recipe_version}
    body = _index_text(entries, heading="Categories")
    (root / _INDEX).write_text(serialize_okf(frontmatter, body), encoding="utf-8")


def compile_bundle(
    texts: dict[str, str],
    enrichment: dict[str, EnrichedClause],
    out_root: Path,
    *,
    recipe_version: str = RECIPE_VERSION,
    okf_version: str = OKF_VERSION,
) -> OkfBundleManifest:
    """Compile the whole bundle. Content-hash gated: an unchanged corpus+recipe returns without rewriting."""
    out_root = Path(out_root)
    content_hash = _bundle_hash(texts, enrichment, recipe_version)
    manifest_path = out_root / _MANIFEST
    if manifest_path.exists():
        prior = OkfBundleManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if prior.content_hash == content_hash and prior.recipe_version == recipe_version:
            return prior  # unchanged corpus + recipe -> effectively no work

    out_root.mkdir(parents=True, exist_ok=True)
    categories: dict[str, int] = {}
    touched_dirs: set[Path] = set()
    for chunk_id, text in sorted(texts.items()):
        e = enrichment[chunk_id]
        category_dir = out_root / _dir_for(e)
        category_dir.mkdir(parents=True, exist_ok=True)
        (category_dir / f"{_source_doc_id(chunk_id)}.md").write_text(
            _concept_document(chunk_id, text, e), encoding="utf-8"
        )
        label = e.category if e.categorized else UNCATEGORIZED
        categories[label] = categories.get(label, 0) + 1
        touched_dirs.add(category_dir)

    for category_dir in sorted(touched_dirs):
        _write_category_index(category_dir)
    _write_root_index(out_root, okf_version=okf_version, recipe_version=recipe_version)

    manifest = OkfBundleManifest(
        okf_version=okf_version,
        recipe_version=recipe_version,
        n_clauses=len(texts),
        n_categorized=sum(1 for e in enrichment.values() if e.categorized),
        categories=dict(sorted(categories.items())),
        content_hash=content_hash,
    )
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def recompile_category(
    out_root: Path,
    category_label: str,
    texts: dict[str, str],
    enrichment: dict[str, EnrichedClause],
    *,
    recipe_version: str = RECIPE_VERSION,
    okf_version: str = OKF_VERSION,
) -> None:
    """Rewrite a single category subtree (its clause files + index) and refresh the root index only.

    T51's fast-iteration path: a signpost-recipe change for one category costs one subtree, not a full
    rebuild. `texts`/`enrichment` hold just that category's clauses.
    """
    out_root = Path(out_root)
    category_dir = out_root / _slug(category_label)
    category_dir.mkdir(parents=True, exist_ok=True)
    for chunk_id, text in sorted(texts.items()):
        (category_dir / f"{_source_doc_id(chunk_id)}.md").write_text(
            _concept_document(chunk_id, text, enrichment[chunk_id]), encoding="utf-8"
        )
    _write_category_index(category_dir)
    _write_root_index(out_root, okf_version=okf_version, recipe_version=recipe_version)


def register_okf_compile(registry: CapabilityRegistry) -> None:
    """Register `okf_compile` (FR-K.1-K.4): an in-process `function`, category-3 (no ARD manifest)."""
    registry.register(
        "okf_compile",
        contract=OkfBundleManifest,
        kind="function",
        display_name="OKF bundle compile (chunk-only, signpost-enriched)",
    )


# --- ACORD compile driver (the RAC verify entrypoint: uv run python -m rag_wright.okf.compile) ----

_ACORD_SIDECAR = Path("data/acord/chunk_text")
_ACORD_OUT = Path("data/acord/okf/bundle")
_ACORD_ENRICH_CACHE = Path("data/acord/okf/enrichment.json")


def _load_sidecar_texts(sidecar_root: Path) -> dict[str, str]:
    """All ingested chunk texts as {chunk_id: text} by reading the T40 sidecar (one JSON file per doc)."""
    texts: dict[str, str] = {}
    for path in sorted(sidecar_root.glob("*.json")):
        texts.update(json.loads(path.read_text(encoding="utf-8")))
    return texts


def _fallback_enrichment(chunk_id: str, text: str) -> EnrichedClause:
    """Deterministic enrichment for a clause the model could not classify: uncategorized, first-sentence desc.

    Guarantees every ingested clause gets a bundle file (RAC-46) even when the cheap model persistently fails
    on it. These land in `_uncategorized/` and their count is reported, so the fallback is visible, not silent.
    """
    first = " ".join(text.split())[:120]
    return EnrichedClause(chunk_id=chunk_id, category="", description=first or "(no description)", categorized=False)


def _load_env() -> None:
    """Load `.env` into the environment for the CLI run (the seam reads OPENROUTER_* from os.environ).

    Minimal parser mirroring conftest, so no new dependency and no reliance on the pytest env hook.
    """
    import os

    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    import asyncio

    from rag_wright.okf.enrich import (
        SeamClassifier,
        enrich_all,
        load_enrich_cache,
        save_enrich_cache,
    )
    from rag_wright.okf.lint import lint_bundle

    _load_env()
    texts = _load_sidecar_texts(_ACORD_SIDECAR)
    print(f"[okf_compile] {len(texts)} ingested clauses from {_ACORD_SIDECAR}")

    cache = load_enrich_cache(_ACORD_ENRICH_CACHE, RECIPE_VERSION)
    enrichment: dict[str, EnrichedClause] = cache
    if len(enrichment) < len(texts):
        SeamClassifier()(next(iter(texts.values())))  # preflight: fail loud on a systemic error (bad key/model)
    for attempt in range(1, 8):  # resumable: each pass gates on the cache, retries only what is still missing
        before = len(enrichment)
        enrichment = asyncio.run(enrich_all(texts, SeamClassifier(), concurrency=12, cache=enrichment))
        save_enrich_cache(_ACORD_ENRICH_CACHE, RECIPE_VERSION, enrichment)  # LLM results only (no fallbacks)
        print(f"[okf_compile] enriched {len(enrichment)}/{len(texts)} (pass {attempt})")
        if len(enrichment) == len(texts) or len(enrichment) == before:
            break  # done, or a pass made no progress -> the residue goes to the deterministic fallback

    missing = [cid for cid in texts if cid not in enrichment]
    if missing:
        if len(enrichment) < len(texts) // 2:  # a majority failing is systemic, not a stubborn tail
            raise RuntimeError(
                f"only {len(enrichment)}/{len(texts)} clauses enriched by the model; aborting (systemic issue)"
            )
        print(f"[okf_compile] {len(missing)} clauses fell back to deterministic (uncategorized) enrichment")
        for cid in missing:  # every ingested clause must get a bundle file (RAC-46); fallbacks are not cached
            enrichment[cid] = _fallback_enrichment(cid, texts[cid])

    manifest = compile_bundle(texts, enrichment, _ACORD_OUT)
    print(f"[okf_compile] bundle: {manifest.n_clauses} clauses, {manifest.n_categorized} categorized")
    for label, n in manifest.categories.items():
        print(f"             {n:4d}  {label}")
    report = lint_bundle(_ACORD_OUT)
    print(f"[okf_compile] lint: passes={report.passes} | orphan_rate={report.orphan_rate:.3f} | "
          f"description_coverage={report.description_coverage:.3f} | "
          f"broken_link_ratio={report.broken_link_ratio:.3f}")
    print(f"[okf_compile] wrote {_ACORD_OUT}")


if __name__ == "__main__":
    main()
