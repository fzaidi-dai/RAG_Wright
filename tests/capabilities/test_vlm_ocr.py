"""Issue 0009-VLM: VLM-based OCR via a remote vision model (OpenRouter by default) -- the escalation for
degraded scans that character-based OCR cannot read. The model is the VISION_OCR role -- the product LLM
(Qwen3.8-27B, a vision model) since ING-4c, swappable via RAG_MODEL_VISION_OCR -- resolved through the model-profile
seam (endpoint, served id, free-text reasoning flags). Hermetic: no live API -- the docling converter is injected."""
from __future__ import annotations

from rag_wright.capabilities.vlm_ocr import openrouter_vlm_options, vlm_ocr
from rag_wright.models import profiles
from rag_wright.models.profiles import ModelRole, model_for


def test_vision_ocr_role_defaults_to_the_product_llm_and_is_overridable(monkeypatch):
    monkeypatch.delenv("RAG_MODEL_VISION_OCR", raising=False)
    monkeypatch.delenv("RAG_MODEL_ALL", raising=False)
    assert model_for(ModelRole.VISION_OCR) == profiles._PRODUCT_LLM
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
    # the role's PROFILE is resolved: OpenRouter's served id, free-text reasoning OFF, the profile's provider pin
    assert o.params["model"] == "qwen/qwen3.8-27b" and "openrouter.ai" in str(o.url)
    assert o.params["reasoning"] == {"enabled": False}
    assert o.params["provider"]["only"] == ["deepinfra/bf16"]


def test_a_self_hosted_profile_routes_ocr_to_its_vllm_server(monkeypatch):
    monkeypatch.setenv("VLLM_BASE_URL", "https://example--rw-qwen3-modal-serve.modal.run/v1")
    monkeypatch.setenv("VLLM_API_KEY", "vk")
    o = openrouter_vlm_options("qwen3.8-27b-modal")
    assert str(o.url) == "https://example--rw-qwen3-modal-serve.modal.run/v1/chat/completions"
    assert o.params["model"] == "Qwen/Qwen3.8-27B" and o.headers["Authorization"] == "Bearer vk"
    assert o.params["chat_template_kwargs"] == {"enable_thinking": False}


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


def test_vlm_availability_follows_the_role_profile_not_a_hardcoded_provider(monkeypatch):
    from rag_wright.capabilities.parsing import _vlm_available

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("RAG_MODEL_ALL", raising=False)
    monkeypatch.setenv("RAG_MODEL_VISION_OCR", "qwen3.8-27b-modal")  # a self-hosted vLLM profile
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    assert _vlm_available() is False                       # no server configured -> graceful degrade
    monkeypatch.setenv("VLLM_BASE_URL", "https://example.modal.run/v1")
    assert _vlm_available() is True                        # usable without any OpenRouter key
    monkeypatch.setenv("RAG_MODEL_VISION_OCR", "qwen3.8-27b-modal-or")
    assert _vlm_available() is False                       # an OpenRouter profile still needs its key


def test_every_ocr_page_is_metered_as_one_vision_call(monkeypatch):
    # docling makes the VLM request itself, so the engine meters it: one uncosted call per page transcribed
    # (else OCR spend is invisible to `measure_usage` and to any credit guard built on it).
    from rag_wright.api import measure_usage
    from rag_wright.capabilities.vlm_ocr import VlmOCRParser

    monkeypatch.delenv("RAG_MODEL_VISION_OCR", raising=False)
    monkeypatch.delenv("RAG_MODEL_ALL", raising=False)

    class _Paged:
        def convert(self, path, page_range=None):
            class _Res:
                document = type("D", (), {"pages": {1: None, 2: None, 3: None}})()
            return _Res()

    with measure_usage() as u:
        VlmOCRParser(_Paged()).parse_range("scan.pdf", (1, 3))
    assert u.calls == 3 and u.calls_without_cost == 3
    assert list(u.by_model) == [model_for(ModelRole.VISION_OCR)]
