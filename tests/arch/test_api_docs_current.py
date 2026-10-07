"""The generated API reference (docs/api/README.md) must match the live `rag_wright.api` surface: it drifted once
(ING-4c's `TaggedSpan.primary`, `build_ingestion(document_hook=)`) while claiming CI guarded it, and no CI did.
Regenerate with `bash scripts/build_api_docs.sh`."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_the_api_reference_matches_the_live_public_surface():
    spec = importlib.util.spec_from_file_location("build_api_docs", _ROOT / "scripts" / "build_api_docs.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    committed = (_ROOT / "docs" / "api" / "README.md").read_text(encoding="utf-8")
    assert committed == gen.render(), "docs/api/README.md is stale -- run `bash scripts/build_api_docs.sh`"
