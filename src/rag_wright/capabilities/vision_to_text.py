"""Vision-to-text (FR-C.9, split from `generation` by ADR-0014, T29), SKILL-SPLIT: image/scan -> text.

The GENERAL-role model (it must accept images) transcribes a scanned filing's images to text, exercising the
image-only PDF subset (ADR-0002). This is a SINGLE grounded vision-language act -- the ingestion-side twin of
answer `generation` -- so it is an `agent_skill`, not a function (per the capability-architecture rubric: a
function is deterministic and takes no model; a single LLM act is an authored skill). The transcription METHOD
is authored as `skills/vision_to_text/SKILL.md`; `SeamVisionModel` is its runtime, sending the image as an
OpenAI-compatible multimodal message (a base64 data URI) on the GENERAL role via the model-profile seam -- no
provider or model flag lives here.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Protocol, runtime_checkable

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model

_SKILL_PATH = Path(__file__).parents[1] / "skills" / "vision_to_text" / "SKILL.md"


class VisionTranscription(BaseModel):
    """The vision-to-text capability's output contract: the transcribed text of an image."""

    text: str


def transcription_method() -> str:
    """The transcription method (the `vision_to_text` SKILL body, YAML frontmatter stripped) used as the
    vision prompt. Authored knowledge (skills/vision_to_text/SKILL.md), not a hardcoded string."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        marker = text.find("\n---", 3)
        if marker != -1:
            text = text[marker + 4 :]
    return text.strip()


@runtime_checkable
class VisionModel(Protocol):
    """The vision seam: transcribe an image's text. `SeamVisionModel` binds it; tests stub it."""

    def image_to_text(self, image: bytes, *, media_type: str) -> str: ...


class SeamVisionModel:
    """The `vision_to_text` SKILL's runtime: a multimodal call on the GENERAL-role model via the
    seam, with the SKILL.md method as the instruction."""

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or model_for(ModelRole.GENERAL)
        self._method = transcription_method()

    def image_to_text(self, image: bytes, *, media_type: str = "image/png") -> str:
        data_uri = f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"
        message = HumanMessage(content=[
            {"type": "text", "text": self._method},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ])
        result = build_model(self._model_id).invoke([message])
        return result.content if hasattr(result, "content") else str(result)


def vision_to_text(image: bytes, *, model: VisionModel, media_type: str = "image/png") -> str:
    """Apply the `vision_to_text` SKILL: transcribe a scanned image to text (ingestion-side, image-only
    filings). `model` is the skill runtime (SeamVisionModel in production; a stub in tests)."""
    return model.image_to_text(image, media_type=media_type)


def register_vision_to_text(registry: CapabilityRegistry) -> None:
    """Register `vision_to_text` as an AGENT_SKILL (FR-C.9, split from `generation` by ADR-0014; SKILL-SPLIT): a
    single grounded vision-language act, authored as `skills/vision_to_text/SKILL.md` and applied via the seam.
    Typed output = `VisionTranscription`."""
    registry.register(
        "vision_to_text",
        contract=VisionTranscription,
        kind="agent_skill",
        display_name="Vision-to-text (scanned-image transcription; authored skill)",
    )
