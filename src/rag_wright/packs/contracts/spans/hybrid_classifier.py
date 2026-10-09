"""Step-5b: the LLM-hybrid function classifier. LegalBERT predicts every span (fast, local); when its top-2
are confusable SIBLINGS in a dev-validated ROUTE family, the span is routed to a Gemma confirm among that
family (the LLM distinguishes those clause types better -- Notice-Period, Irrevocable, No-Solicit went from
~0 to 0.33-0.67). Non-routed spans keep LegalBERT (stronger on the other families, e.g. Cap-vs-Uncapped).
Only ~3% of spans route, so the LLM cost is small. Confirmer defaults to GEMMA (benchmarked); injectable for
tests. The routed LLM calls in one `classify` batch run CONCURRENTLY (the standing eval/serve concurrency rule).
"""

from __future__ import annotations

import asyncio
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from rag_wright.packs.contracts.schemas.function import canonical_function
from rag_wright.api import ModelRole
from rag_wright.pack_sdk import model_for
from rag_wright.pack_sdk import build_structured
from rag_wright.packs.contracts.spans.function_families import route_family

NONE_LABEL = "NONE"


class _FamilyLabel(BaseModel):
    label: str


_PROMPT = (
    "A clause classifier is unsure which of these RELATED clause types a span is:\n- {opts}\n\n"
    "Decide which ONE it actually is by its operative meaning, or NONE if it is none of them. Respond with "
    "EXACTLY one of the type strings above, or NONE.\n\nSpan:\n{text}"
)


@runtime_checkable
class FamilyConfirmer(Protocol):
    def __call__(self, text: str, candidates: tuple[str, ...]) -> str: ...


class SeamFamilyConfirmer:
    """The real confirmer: structured output on GENERAL (Gemma). Returns a validated family label or NONE
    (an off-family / hallucinated answer -> NONE). Retries a transient bare `None`."""

    def __init__(self, model_id: Optional[str] = None, *, retries: int = 3) -> None:
        self._runnable = build_structured(model_id or model_for(ModelRole.GENERAL), _FamilyLabel)
        self._retries = retries

    def __call__(self, text: str, candidates: tuple[str, ...]) -> str:
        prompt = _PROMPT.format(opts="\n- ".join(candidates), text=text[:2000])
        for _ in range(self._retries):
            try:
                v = self._runnable.invoke(prompt)
            except Exception:  # noqa: BLE001 - transient provider/parse error; retry
                continue
            if v is not None:
                canon = canonical_function(v.label)
                return canon if canon in candidates else NONE_LABEL
        return NONE_LABEL


class HybridFunctionClassifier:
    """LegalBERT + selective LLM fallback on confusable-sibling spans. `classify` returns canonical labels.

    `targets` (optional) narrows routing to TARGETED mode: route only when a target class is in the LegalBERT
    top-2 (and the top-2 are siblings in a route family). This rescues the rare targets while leaving the
    common/strong classes untouched -- fewer LLM calls, zero risk to what already works. `targets=None` routes
    every sibling-ambiguous span in a route family."""

    def __init__(self, bert_classifier, confirmer: Optional[FamilyConfirmer] = None, *,
                 targets: Optional[frozenset[str]] = None, concurrency: int = 8):
        self._bert = bert_classifier
        self._confirm = confirmer if confirmer is not None else SeamFamilyConfirmer()
        self._targets = targets
        self._concurrency = concurrency

    def classify(self, texts: list[str], *, batch_size: int = 32) -> list[str]:
        if not texts:
            return []
        topk = self._bert.classify_topk(texts, k=2, batch_size=batch_size)
        labels = [canonical_function(row[0]) or row[0] for row in topk]  # LegalBERT top-1 (canonical)
        routed = []  # (index, family) where the top-2 are siblings in a ROUTE family
        for i, row in enumerate(topk):
            fam = route_family(labels[i])
            top2 = canonical_function(row[1]) if len(row) > 1 else None
            if fam is not None and top2 in fam:
                if self._targets is not None and labels[i] not in self._targets and top2 not in self._targets:
                    continue  # TARGETED: skip unless a rare target is one of the top-2
                routed.append((i, fam))
        if not routed:
            return labels

        async def _run():
            sem = asyncio.Semaphore(self._concurrency)

            async def _one(i, fam):
                async with sem:
                    return i, await asyncio.to_thread(self._confirm, texts[i], fam)

            return await asyncio.gather(*[_one(i, fam) for i, fam in routed])

        for i, lab in asyncio.run(_run()):
            labels[i] = lab
        return labels
