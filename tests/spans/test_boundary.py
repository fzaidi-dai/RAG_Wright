"""Bug-A: provision-boundary decision = deterministic-first + a decision-model fallback for the UNCERTAIN residue
(spans.boundary). Hermetic: an injected fake decider, so no model and no network."""
from __future__ import annotations

from rag_wright.spans.boundary import adecide_provision_starts

_TEXTS = [
    "Section 8. Limitation of Liability.",                       # deterministic START
    "the parties agree to indemnify each other for any losses",  # deterministic CONTINUE
    "Limitation of Liability:",                                  # UNCERTAIN (colon heading) -> decider
]


async def test_deterministic_only_when_no_decider():
    # No decision model: start/continue decided for free; the uncertain residue degrades to NOT-a-start (fold in).
    assert await adecide_provision_starts(_TEXTS, decider=None) == [True, False, False]


async def test_decider_sees_only_the_uncertain_residue():
    seen: dict = {}

    async def fake(residue_texts):
        seen["texts"] = residue_texts
        return [True] * len(residue_texts)  # the model judges each uncertain line a provision start

    starts = await adecide_provision_starts(_TEXTS, decider=fake)
    assert seen["texts"] == ["Limitation of Liability:"]  # ONLY the uncertain span reached the model
    assert starts == [True, False, True]                   # deterministic start kept; residue resolved to start


async def test_decider_error_degrades_to_deterministic():
    async def boom(_residue):
        raise RuntimeError("decision model unavailable")

    assert await adecide_provision_starts(["Limitation of Liability:"], decider=boom) == [False]
