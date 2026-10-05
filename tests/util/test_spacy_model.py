"""PREP-1.1: spaCy is an optional runtime asset (extra `rag-wright[ner]` + a model
download), never a packaged dependency. The loader centralizes the lazy import and
emits ONE actionable error when spaCy or the model is missing. Hermetic: a fake
`spacy` is injected into sys.modules, so these never require the extra installed.
"""
from __future__ import annotations

import sys
import types

import pytest

from rag_wright.util.spacy_model import DEFAULT_SPACY_MODEL, load_spacy_model


def test_missing_spacy_raises_actionable_error(monkeypatch):
    # The extra is not installed -> `import spacy` fails.
    monkeypatch.setitem(sys.modules, "spacy", None)
    with pytest.raises(ImportError) as ei:
        load_spacy_model()
    msg = str(ei.value)
    assert "rag-wright[ner]" in msg
    assert "spacy download" in msg
    assert DEFAULT_SPACY_MODEL in msg


def test_missing_model_raises_actionable_error(monkeypatch):
    # spaCy present, but the model was never downloaded -> spacy.load raises OSError.
    fake = types.ModuleType("spacy")
    fake.load = lambda name, **kw: (_ for _ in ()).throw(OSError(f"Can't find model '{name}'"))
    monkeypatch.setitem(sys.modules, "spacy", fake)
    with pytest.raises(OSError) as ei:
        load_spacy_model("en_core_web_sm")
    msg = str(ei.value)
    assert "rag-wright[ner]" in msg
    assert "spacy download" in msg


def test_honors_rag_spacy_model_env(monkeypatch):
    # ADR-0012 seam: the model name is configurable via RAG_SPACY_MODEL.
    captured = {}
    fake = types.ModuleType("spacy")

    def _load(name, **kw):
        captured["name"] = name
        return f"nlp:{name}"

    fake.load = _load
    monkeypatch.setitem(sys.modules, "spacy", fake)
    monkeypatch.setenv("RAG_SPACY_MODEL", "en_core_web_md")
    out = load_spacy_model()
    assert captured["name"] == "en_core_web_md"
    assert out == "nlp:en_core_web_md"
