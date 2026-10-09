"""PS-R5b: the RLM chunking prompts are domain-neutral; a domain adds its own wording with `guidance=`, which the
reference contracts pack does (so its chunking keeps the contract framing)."""
from __future__ import annotations

import re

from rag_wright.capabilities import rlm_chunking as rc

_DOMAIN = re.compile(r"\bcontracts?\b|\bclauses?\b", re.IGNORECASE)


class _Doc:
    """A parsed-document stand-in with three structural items."""


def _items(monkeypatch):
    monkeypatch.setattr(rc, "_document_items", lambda _d: [
        {"index": i, "text": f"item {i}"} for i in range(3)])


def test_the_prompts_name_no_domain():
    for prompt in (rc._DISCOVERY_INSTRUCTIONS, rc._SINGLE_CALL_PROMPT, rc._CUT_PROMPT, rc._SUMMARIZE_PROMPT):
        assert not _DOMAIN.search(prompt), prompt


def test_discoverer_prompts_carry_the_domain_guidance(monkeypatch):
    _items(monkeypatch)
    for disc in (rc.TagBoundaryDiscoverer("m", guidance="DOMAIN-HINT-3"),
                 rc.SingleCallBoundaryDiscoverer("m", guidance="DOMAIN-HINT-3")):
        prompt, n = disc._prompt(_Doc())
        assert n == 3 and "Domain guidance: DOMAIN-HINT-3" in prompt and prompt.rstrip().endswith("[2] item 2")
    plain, _ = rc.TagBoundaryDiscoverer("m")._prompt(_Doc())
    assert "Domain guidance" not in plain


def test_the_structural_discoverer_hands_its_guidance_to_the_model_fallback():
    disc = rc.StructuralModelFallbackDiscoverer("m", guidance="DOMAIN-HINT-4")
    assert disc._fallback._guidance == "DOMAIN-HINT-4"


def test_the_summarizer_prompt_carries_the_domain_guidance(monkeypatch):
    seen = {}

    class _R:
        def invoke(self, prompt):
            seen["prompt"] = prompt
            return rc._Summary(summary="s")

    monkeypatch.setattr(rc, "build_structured", lambda *_a, **_k: _R())
    assert rc.SeamSummarizer("m", guidance="DOMAIN-HINT-5").summarize("text") == "s"
    assert "DOMAIN-HINT-5" in seen["prompt"] and seen["prompt"].endswith("text")


def test_the_reference_pack_chunks_with_its_contract_guidance():
    from rag_wright.packs.contracts.skills.guidance import contract_guidance

    assert "clause" in contract_guidance("chunking")
