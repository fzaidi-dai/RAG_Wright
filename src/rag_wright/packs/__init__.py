"""The engine's REFERENCE PACK (ING-8c, ADR-0124): the contract + compliance worked example, as two domain packs.

`packs.contracts` is the contract domain; `packs.compliance` is built on it. Nothing outside `rag_wright.packs`
imports a pack (an import-linter contract in `pyproject.toml`), so the generic engine stays domain-free. Load the
pack with `load_reference_pack()`; a product supplies its own pack in its own repo instead.
"""
