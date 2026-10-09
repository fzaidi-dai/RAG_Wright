"""PS-R5a: the contracts pack's domain guidance for the engine's domain-neutral methods (relevance judgment, answer
generation). The engine method stays generic; the pack appends what contracts look like (ADR-0066: generic source +
domain overlay)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def contract_guidance(name: str) -> str:
    """The pack's guidance for the engine method `name` (`relevance`, `generation`)."""
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
