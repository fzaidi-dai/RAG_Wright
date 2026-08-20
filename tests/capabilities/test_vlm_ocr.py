"""Issue 0009-VLM: VLM-based OCR via a remote vision model (OpenRouter by default) -- the escalation for
degraded scans that character-based OCR cannot read. The model is the VISION_OCR role (default Gemma-4,
swappable via RAG_MODEL_VISION_OCR). Hermetic: no live API -- the docling converter is injected."""
from __future__ import annotations

from rag_wright.capabilities.vlm_ocr import openrouter_vlm_options, vlm_ocr
from rag_wright.models.profiles import DEFAULT_GENERAL, ModelRole, model_for


def test_vision_ocr_role_defaults_to_gemma4_and_is_overridable(monkeypatch):
    monkeypatch.delenv("RAG_MODEL_VISION_OCR", raising=False)
    monkeypatch.delenv("RAG_MODEL_ALL", raising=False)
    assert model_for(ModelRole.VISION_OCR) == DEFAULT_GENERAL == "google/gemma-4-31b-it"
    monkeypatch.setenv("RAG_MODEL_VISION_OCR", "some/other-vlm")  # swappable via the seam
    assert model_for(ModelRole.VISION_OCR) == "some/other-vlm"


def test_openrouter_vlm_options_wires_model_url_and_auth():
    o = openrouter_vlm_options("qwen/qwen-2.5-vl-72b-instruct", api_key="sk-test")
    assert "openrouter.ai" in str(o.url)
    assert o.headers["Authorization"] == "Bearer sk-test"
    assert o.params["model"] == "qwen/qwen-2.5-vl-72b-instruct"


def test_openrouter_vlm_options_defaults_to_the_vision_ocr_role(monkeypatch):
    monkeypatch.delenv("RAG_MODEL_VISION_OCR", raising=False)
    monkeypatch.delenv("RAG_MODEL_ALL", raising=False)
    o = openrouter_vlm_options(api_key="k")
    assert o.params["model"] == model_for(ModelRole.VISION_OCR)  # Gemma-4 by default


class _FakeConverter:
    def __init__(self, markdown: str) -> None:
        self._md = markdown
        self.converted_path = None

    def convert(self, path):
        self.converted_path = path

        class _Doc:
            def export_to_markdown(_self):
                return self._md

        class _Res:
            document = _Doc()

        return _Res()


def test_vlm_ocr_reads_bytes_via_the_injected_converter():
    conv = _FakeConverter("MASTER SERVICES AGREEMENT\n1. LIMITATION OF LIABILITY ...")
    out = vlm_ocr(b"%PDF-1.4 degraded scan", "scan.pdf", converter=conv)
    assert "MASTER SERVICES AGREEMENT" in out
    assert conv.converted_path and conv.converted_path.endswith("scan.pdf")  # bytes were written + parsed
