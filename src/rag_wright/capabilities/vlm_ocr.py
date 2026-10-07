"""Issue 0009-VLM: VLM-based OCR via a remote vision model -- the escalation for DEGRADED scans that
character-based OCR cannot read (severe blur / faded ink), where a strong VLM reads the text by language
context like a human (benchmark: Gemma-4 char_sim 0.991 on the heavy scan vs ~0.01-0.10 for OCR; see
docs/eval/ocr_benchmark.md).

docling's `ApiVlmOptions` points at any OpenAI-compatible endpoint. The model is the `VISION_OCR` role -- the
product LLM (Qwen3.8-27B, a vision model) since ING-4c, swappable via `RAG_MODEL_VISION_OCR` -- and its PROFILE
decides the endpoint (OpenRouter or a self-hosted vLLM/Modal server), the served id, and the free-text flags
(reasoning off, provider pin), so the escalation model is chosen through the model-profile seam, never hardcoded
(the standing model rule).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Optional

from rag_wright.models.profiles import ModelRole, model_for, profile_for
from rag_wright.models.seam import resolve_connection
from rag_wright.models.usage import record_usage

_OCR_PROMPT = ("Transcribe ALL text from this document page exactly as it appears, preserving the reading order. "
               "Output only the transcribed text as markdown, with no commentary.")


def openrouter_vlm_options(model_id: Optional[str] = None, *, api_key: Optional[str] = None,
                           base_url: Optional[str] = None, scale: float = 3.0,
                           prompt: Optional[str] = None, timeout: float = 180.0):
    """A docling `ApiVlmOptions` for a remote VLM OCR call. Model defaults to the VISION_OCR role; its profile
    resolves the endpoint, key, served id, and the free-text request flags (`extra_body` + `text_extra_body`).
    `api_key`/`base_url` override the resolved ones; `scale` renders the page at higher resolution for the model."""
    from docling.datamodel.pipeline_options import ApiVlmOptions, ResponseFormat

    model_id = model_id or model_for(ModelRole.VISION_OCR)
    profile, conn = profile_for(model_id), resolve_connection(model_id)
    url = base_url or f"{conn.base_url.rstrip('/')}/chat/completions"
    key = api_key if api_key is not None else (conn.api_key or "")
    params = {"model": conn.served_model_id, "max_tokens": 8192, **(profile.extra_body or {}),
              **(profile.text_extra_body or {})}  # OCR is a free-text call
    return ApiVlmOptions(
        url=url, headers={"Authorization": f"Bearer {key}"},
        params=params, prompt=prompt or _OCR_PROMPT,
        response_format=ResponseFormat.MARKDOWN, scale=scale, timeout=timeout)


def build_vlm_ocr_converter(vlm_options: Any = None, *, model_id: Optional[str] = None, **kw):
    """A docling `DocumentConverter` whose PDF pipeline is the VLM pipeline over `vlm_options` (default: the
    OpenRouter VLM). `enable_remote_services` is set so docling may call the API."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import VlmPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline

    opts = vlm_options or openrouter_vlm_options(model_id=model_id, **kw)
    vopts = VlmPipelineOptions(vlm_options=opts)
    vopts.enable_remote_services = True  # required for an API-based VLM
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_cls=VlmPipeline, pipeline_options=vopts)})


def _metered(document: Any, model_id: Optional[str] = None) -> Any:
    """docling makes the VLM request itself, so meter it here: one call per page transcribed, uncosted (docling
    does not surface tokens/cost), keyed by the engine model id -- so OCR spend shows in `measure_usage`."""
    model_id = model_id or model_for(ModelRole.VISION_OCR)
    for _ in range(len(getattr(document, "pages", None) or {}) or 1):
        record_usage(model_id)
    return document


def vlm_ocr(data: bytes, name: str = "scan.pdf", *, converter: Any = None, model_id: Optional[str] = None) -> str:
    """OCR a document's raw bytes via the VLM -> markdown text. `converter` is injectable (hermetic tests);
    production builds the OpenRouter converter for the VISION_OCR model."""
    conv = converter or build_vlm_ocr_converter(model_id=model_id)
    tmp = Path(tempfile.mkdtemp(prefix="vlmocr_")) / name
    tmp.write_bytes(data)
    return _metered(conv.convert(str(tmp)).document, model_id).export_to_markdown()


class VlmOCRParser:
    """A `Parser` (convert(source) -> DoclingDocument) backed by the remote VLM converter -- the 0009 escalation
    parser the tiered path routes degraded scans to. The converter is injectable for tests."""

    def __init__(self, converter: Any = None) -> None:
        self._converter = converter

    def convert(self, source):
        conv = self._converter or build_vlm_ocr_converter()
        return _metered(conv.convert(str(source)).document)

    def parse_range(self, source, page_range):
        """PARSE-3: VLM-parse only pages `page_range` (1-based, inclusive), so the tiered path escalates just the
        degraded pages instead of the whole document."""
        conv = self._converter or build_vlm_ocr_converter()
        return _metered(conv.convert(str(source), page_range=page_range).document)


def register_vlm_ocr(registry) -> None:
    """0009-VLM: register `vlm_ocr` (function; document bytes -> transcribed text via a remote VLM)."""
    registry.register("vlm_ocr", contract=str, kind="function",
                      display_name="VLM OCR (degraded-scan escalation via the VISION_OCR model profile)")
