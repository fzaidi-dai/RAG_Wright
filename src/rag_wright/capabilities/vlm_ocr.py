"""Issue 0009-VLM: VLM-based OCR via a remote vision model -- the escalation for DEGRADED scans that
character-based OCR cannot read (severe blur / faded ink), where a strong VLM reads the text by language
context like a human (benchmark: Gemma-4 char_sim 0.991 on the heavy scan vs ~0.01-0.10 for OCR; see
docs/eval/ocr_benchmark.md).

docling's `ApiVlmOptions` points at any OpenAI-compatible endpoint; OpenRouter by default (the same provider we
already use for Gemma-4 -- no Modal, no local model). The model is the `VISION_OCR` role (default Gemma-4,
swappable via `RAG_MODEL_VISION_OCR`), so the escalation model is chosen through the model-profile seam, never
hardcoded (the standing model rule).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from rag_wright.models.profiles import ModelRole, model_for

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_OCR_PROMPT = ("Transcribe ALL text from this document page exactly as it appears, preserving the reading order. "
               "Output only the transcribed text as markdown, with no commentary.")


def openrouter_vlm_options(model_id: Optional[str] = None, *, api_key: Optional[str] = None,
                           base_url: str = _OPENROUTER_URL, scale: float = 3.0,
                           prompt: Optional[str] = None, timeout: float = 180.0):
    """A docling `ApiVlmOptions` for a remote VLM OCR call. Model defaults to the VISION_OCR role (Gemma-4);
    endpoint is OpenRouter; `scale` renders the page at higher resolution for the model."""
    from docling.datamodel.pipeline_options import ApiVlmOptions, ResponseFormat

    model_id = model_id or model_for(ModelRole.VISION_OCR)
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    return ApiVlmOptions(
        url=base_url, headers={"Authorization": f"Bearer {key}"},
        params={"model": model_id, "max_tokens": 8192}, prompt=prompt or _OCR_PROMPT,
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


def vlm_ocr(data: bytes, name: str = "scan.pdf", *, converter: Any = None, model_id: Optional[str] = None) -> str:
    """OCR a document's raw bytes via the VLM -> markdown text. `converter` is injectable (hermetic tests);
    production builds the OpenRouter converter for the VISION_OCR model."""
    conv = converter or build_vlm_ocr_converter(model_id=model_id)
    tmp = Path(tempfile.mkdtemp(prefix="vlmocr_")) / name
    tmp.write_bytes(data)
    return conv.convert(str(tmp)).document.export_to_markdown()


def register_vlm_ocr(registry) -> None:
    """0009-VLM: register `vlm_ocr` (function; document bytes -> transcribed text via a remote VLM)."""
    registry.register("vlm_ocr", contract=str, kind="function",
                      display_name="VLM OCR (degraded-scan escalation via OpenRouter, default Gemma-4)")
