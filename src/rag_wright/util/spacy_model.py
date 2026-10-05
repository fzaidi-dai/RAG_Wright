"""Optional spaCy model loader (a runtime asset, not a packaged dependency).

spaCy is an OPTIONAL extra: install it with ``uv pip install 'rag-wright[ner]'``. Its
model (``en_core_web_sm`` by default) is **not** on PyPI, so it is a one-time runtime
download via ``uv run python -m spacy download <model>`` — never a declared or
direct-URL dependency (ADR superseding ADR-0012's wheel-URL pin; see docs/adr/).

This module is the single place the engine (or a consumer) loads a spaCy pipeline. It
imports spaCy lazily so the engine installs and runs without the extra, and raises one
actionable error when spaCy or the model is absent. The model name honors the
``RAG_SPACY_MODEL`` env var (ADR-0012 domain-portability seam), default
``en_core_web_sm``.
"""

from __future__ import annotations

import os

DEFAULT_SPACY_MODEL = "en_core_web_sm"

_INSTALL_HINT = (
    "spaCy NER is an optional extra. Install it and its model:\n"
    "    uv pip install 'rag-wright[ner]'\n"
    "    uv run python -m spacy download {model}\n"
    "(the model name is configurable via RAG_SPACY_MODEL)."
)


def load_spacy_model(model: str | None = None):
    """Load the configured spaCy pipeline, or raise an actionable error.

    ``model`` overrides the env; otherwise ``RAG_SPACY_MODEL`` (default
    ``en_core_web_sm``) is used. Raises ``ImportError`` if the ``ner`` extra is not
    installed, or ``OSError`` if the model has not been downloaded — both messages name
    the exact install/download commands.
    """
    name = model or os.environ.get("RAG_SPACY_MODEL", DEFAULT_SPACY_MODEL)
    try:
        import spacy
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT.format(model=name)) from exc
    try:
        return spacy.load(name)
    except OSError as exc:
        raise OSError(_INSTALL_HINT.format(model=name)) from exc
