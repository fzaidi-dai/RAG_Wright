# ADR-0121: spaCy is an optional extra; its model is a runtime download (supersedes ADR-0012's wheel-URL pin)

- Status: Accepted
- Date: 2026-10-05
- Supersedes: ADR-0012 point 4 (the `en_core_web_sm` dependency mechanism) only; the rest of ADR-0012 stands.
- Context: engine-prep PREP-1.1 (ready the engine for PyPI release).

## Context

ADR-0012 pinned the spaCy model `en_core_web_sm` as a hard dependency resolved through a `[tool.uv.sources]`
direct URL to its GitHub release wheel, "for reproducibility and uv-only discipline." Two things have since made
that the wrong mechanism:

1. **The spaCy NER path was retired (ADR-0035):** `graph_extraction` was re-backed with the docling-graph extractor.
   There is no live `import spacy` / `spacy.load` anywhere in `src`; the model and the `spacy` library were a fully
   dead, heavyweight dependency chain pulled only by our own declarations.
2. **The direct URL forbids a PyPI release.** A `[tool.uv.sources]` URL is uv-only and is not written into wheel
   metadata; PyPI rejects direct-URL dependencies, so `pip install rag-wright` from PyPI could never resolve. This
   is the blocker we had only ever worked around (declare-the-URL for local `uv sync`), never solved.

A model that is distributed only via GitHub releases (not on PyPI) can **never** be a declared dependency — extra
or not. Only the `spacy` library itself is on PyPI and can be an extra.

## Decision

1. **`spacy` moves to an optional extra `rag-wright[ner]`** (`[project.optional-dependencies] ner = ["spacy>=3.8.14"]`),
   out of the core dependency set. The default install is leaner and PyPI-resolvable.
2. **The model is a runtime download, never a dependency.** A consumer who wants NER runs
   `uv run python -m spacy download en_core_web_sm` (the model name is configurable via `RAG_SPACY_MODEL`, the
   ADR-0012 portability seam — swappable to md/lg/trf). The `[tool.uv.sources]` entry is deleted.
3. **One loader, one actionable error.** `rag_wright.util.spacy_model.load_spacy_model(model=None)` imports spaCy
   lazily and raises a single clear error naming the exact install + download commands when either the extra or the
   model is absent. The engine installs and runs without the extra; nothing imports spaCy at module import time.

## Consequences

- `pip install rag-wright` (and a published wheel) resolves with no direct-URL dependency; the built wheel's
  `Requires-Dist` carries `spacy` only under `extra == 'ner'`.
- NER is re-enableable at any time: `uv pip install 'rag-wright[ner]'` + `spacy download <model>`. When a future
  NER capability is actually built, it loads through `load_spacy_model` — the seam is preserved, not deleted.
- The model version is no longer pinned in `uv.lock` (it is a runtime asset). A consumer who needs a reproducible
  model version pins it in their own download step. Acceptable: the model is data, not code, and was dead here.
- ADR-0012's substantive decisions (EntityMention confidence, the spaCy path emits mentions not proximity edges,
  the resolution strategy) are unaffected — only its dependency-packaging mechanism (point 4) is superseded.
