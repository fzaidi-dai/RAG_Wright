"""Vision-to-text (part of the FR-C.9 `generation` capability, T29): image/scan -> text at ingestion.

The Gemma 4 class model (GENERAL role) transcribes a scanned filing's images to text, exercising the
image-only PDF subset (ADR-0002). It is one capability with answer generation (both on the same model
through the model-profile seam), registered under `generation`; this module is the ingestion-side vision
function. The image is sent as an OpenAI-compatible multimodal message (a base64 data URI); no provider
or model flag lives here — the model is the GENERAL role via the seam.
"""

from __future__ import annotations

import base64
from typing import Protocol, runtime_checkable

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model


class VisionTranscription(BaseModel):
    """The vision-to-text capability's output contract: the transcribed text of an image."""

    text: str

_TRANSCRIBE_PROMPT = (
    "Transcribe all text visible in this image exactly, preserving reading order. "
    "Output only the transcribed text."
)


@runtime_checkable
class VisionModel(Protocol):
    """The vision seam: transcribe an image's text. `SeamVisionModel` binds it; tests stub it."""

    def image_to_text(self, image: bytes, *, media_type: str) -> str: ...


class SeamVisionModel:
    """The real vision model: a multimodal call on the GENERAL (Gemma 4 class) model via the seam."""

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)

    def image_to_text(self, image: bytes, *, media_type: str = "image/png") -> str:
        data_uri = f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"
        message = HumanMessage(content=[
            {"type": "text", "text": _TRANSCRIBE_PROMPT},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ])
        result = build_model(self._model_id).invoke([message])
        return result.content if hasattr(result, "content") else str(result)


def vision_to_text(image: bytes, *, model: VisionModel, media_type: str = "image/png") -> str:
    """Transcribe a scanned image to text (ingestion-side, for image-only filings)."""
    return model.image_to_text(image, media_type=media_type)


def register_vision_to_text(registry: CapabilityRegistry) -> None:
    """Register vision-to-text under FR-C.9 (`vision_to_text`, split from `generation`, ADR-0014)."""
    registry.register(
        "vision_to_text",
        contract=VisionTranscription,
        kind="function",
        display_name="Vision-to-text (scanned-image transcription)",
    )
