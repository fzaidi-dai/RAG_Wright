"""ING-9b: the residual (numeric/open) property dimensions on a typed-DECISION model instead of an LLM.

Deterministic CANDIDATE spans (amounts, durations, dates, frequencies, quantities, jurisdiction phrases, cap
formulas, term references, liquidated-damages sentences) are proposed by the patterns below; a decision model then
says what each candidate IS in its provision -- one `choice` per candidate among the residual roles authored in
contract_bridge.ttl (`cbr:ResidualRole`, ADR-0066), ONE call per provision and none when there are no candidates.
Measured on hand-labelled set-A provisions (held-out): 91.8% candidate-role accuracy, 90% of values found, 87% of
emitted values correct -- vs 83.0% / 67% / 83% for the per-provision LLM extraction it replaces. The patterns are
kept exactly as measured; `clean_value` only trims fragment edges at emission.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Awaitable, Callable, Optional

NUMW = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|half|a)")
NUM = (rf"(?:\[\s?\*[\s\*]*\]|\*{{3,}}|\d[\d,\.]*|{NUMW}(?:[\s-]{NUMW})*)"
       rf"(?:\s*\(\s*(?:\d[\d,\.]*|\[[\s\*]+\]|{NUMW}(?:[\s-]{NUMW})*)\s*\))?")
MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
UNIT_T = r"(?:business\s+|calendar\s+|consecutive\s+)?(?:days?|weeks?|months?|years?|quarters?|hours?)"
PATTERNS = {
    "duration": rf"{NUM}\s*[-]?\s*{UNIT_T}(?:'s)?",
    "money": rf"(?:US\$|USD|\$|€|£|EUR|GBP)\s?{NUM}(?:\s*(?:million|billion|thousand))?|{NUM}\s*(?:dollars|euros|USD|U\.S\. dollars)",
    "percent": rf"{NUM}\s*(?:%|percent|per cent)",
    "quantity": rf"{NUM}\s+(?:units?|pieces?|tons?|kilograms?|kg|pounds?|gallons?|liters?|litres?|cases?|orders?|"
                rf"copies|licenses?|subscribers?|users?|seats?|shares?|hours?|sales representatives?)",
    "frequency": r"\b(?:annually|quarterly|monthly|weekly|daily|semi-annually|bi-annually|biannually|"
                 rf"(?:once|twice|{NUM} times?)\s+(?:per|a|in any|each|every|during any)\s+(?:calendar\s+)?(?:year|quarter|month|twelve[\s-]month period|12[\s-]month period)|"
                 r"(?:per|each|every)\s+(?:calendar\s+)?(?:year|quarter|month))\b",
    "jurisdiction": r"(?i:laws?|courts?|jurisdiction|located|court)\s+(?:of|in)\s+(?:the\s+)?((?:State|Commonwealth|Province|Republic|Kingdom|People's Republic)\s+of\s+)?"
                    r"[A-Z][A-Za-z\.]*(?:[\s,]+(?:of\s+|and\s+)?[A-Z][A-Za-z\.]*){0,4}",
    "venue": r"(?:courts?\s+(?:located|sitting|situated)\s+in|[Vv]enue\s+(?:shall\s+be|is)(?:\s+in)?|seat\s+of\s+(?:the\s+)?arbitration\s+(?:shall\s+be|is))\s+"
             r"(?:the\s+)?[A-Z][A-Za-z\.\-]*(?:[\s,]+[A-Z][A-Za-z\.\-]*){0,3}|\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:law|Law)\b",
    "cap_phrase": r"(?:not\s+(?:to\s+)?exceed|in\s+excess\s+of|limited\s+to|exceed(?:ing)?|cap(?:ped)?\s+at|up\s+to\s+a\s+maximum\s+of|maximum\s+of)\s+"
                  r"[^.;:]{3,120}",
    "term_ref": r"\b(?:the\s+)?(?:initial\s+|renewal\s+|then[- ]current\s+|entire\s+)?term\s+of\s+(?:this|the)\s+[A-Za-z]+(?:\s+[A-Z][a-z]+)?|\bthe\s+(?:Initial\s+|Renewal\s+|then[- ]current\s+)?Term\b",
    "date": rf"(?:(?:until|through|before|after|from|commencing(?:\s+on)?|on|by)\s+)?(?:the\s+)?(?:{MONTHS}\s+\d{{1,2}},?\s+\d{{4}}|"
            rf"\d{{1,2}}(?:st|nd|rd|th)?\s+(?:day\s+)?of\s+{MONTHS},?\s+\d{{4}}|{MONTHS}\s+\d{{4}})",
    "count": rf"(?:at\s+least|a\s+minimum\s+of|minimum\s+of|no\s+(?:more|less|fewer)\s+than|not\s+(?:more|less|fewer)\s+than|up\s+to)\s+{NUM}(?:\s+[A-Za-z-]+){{0,4}}"
             rf"|\b\d[\d,]*\s+[a-z]+s\b",
    "bare_number": r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d{3,})(?:\.\d+)?(?![\w.])(?:\s*\([^)]{1,60}\))?",
    "defined_term": r"\b(?:the\s+)?(?:[A-Z][a-z]+\s+)+Term\b|\b(?:in\s+)?perpetu(?:ity|al(?:ly)?)\b",
    "redacted_frequency": rf"{NUM}\s+(?:in|during|per)\s+any\s+(?:calendar\s+|contract\s+)?(?:year|quarter|month)",
    "ld_sentence": r"[^.]*\bliquidated\s+damages\b[^.]*\.?",
}
RX = {k: re.compile(v, re.I if k not in ("jurisdiction", "venue", "date") else 0) for k, v in PATTERNS.items()}




def _positioned(text: str) -> list[tuple[str, str, int, int]]:
    """`(kind, span, start, end)` over the whitespace-flattened text, in document order, de-duplicated
    (case-insensitively); never a bare year."""
    out, seen = [], set()
    flat = " ".join(text.split())
    for kind, rx in RX.items():
        for m in rx.finditer(flat):
            s = m.group(0).strip(" ,;:")
            if len(s) > 1 and s.lower() not in seen:
                seen.add(s.lower())
                out.append((kind, s, m.start(), m.end()))
    return [c for c in sorted(out, key=lambda x: x[2]) if not _YEAR.match(c[1])]


def candidates(text: str) -> list[tuple[str, str]]:
    """`(kind, span)` candidates in document order, de-duplicated (case-insensitively); never a bare year."""
    return [(k, s) for k, s, _a, _b in _positioned(text)]


_YEAR = re.compile(r"^(?:19|20)\d{2}(?:\s+\w+)?$")
_EDGE_WORDS = {"a", "an", "and", "or", "as", "in", "either", "shall", "unless", "of", "to", "the", "will", "is", "be"}


_JURISDICTION_LEAD = re.compile(
    r"^(?:(?:the\s+)?(?:laws?|courts?(?:\s+(?:located|sitting|situated))?|jurisdiction|located|venue(?:\s+(?:shall\s+be|is))?|"
    r"seat\s+of\s+(?:the\s+)?arbitration(?:\s+(?:shall\s+be|is))?)\s+(?:(?:of|in)\s+)?)?(?:the\s+)?"
    r"(?:(?:State|Commonwealth|Province)\s+of\s+)?", re.I)


def clean_value(span: str, role: Optional[str] = None) -> str:
    """Trim connector words a pattern carried onto the EDGES of a candidate ('the Term of this Agreement and' ->
    'the Term of this Agreement', 'a thirty (30) days' -> 'thirty (30) days'); a leading 'the' is kept. A
    jurisdiction is reduced to its place name ('laws of the State of California' -> 'California'), as queries
    filter on the place."""
    if role == "jurisdiction":
        place = re.split(r"\.(?:\s|$)", _JURISDICTION_LEAD.sub("", span.strip()), maxsplit=1)[0].strip(" .,;:")
        place = re.sub(r"\s+(?:law|laws)$", "", place, flags=re.I)
        return place or span.strip(" .,;:")
    words = span.split()
    while len(words) > 1 and words[-1].lower().strip(".,;:") in _EDGE_WORDS:
        words.pop()
    while len(words) > 1 and words[0].lower() in _EDGE_WORDS - {"the"}:
        words.pop(0)
    return " ".join(words).strip(" ,;:")


def _one_per_value(picked: list, flat: str) -> list:
    """One value per stated value: same-role candidates that OVERLAP keep the contained span (the value
    proper, not the phrase around it); a value restated in parentheses ('five million Dollars ($5,000,000)', 'one
    hundred percent (100%)') is kept once -- the form with digits."""
    out: list = []
    for cur in picked:
        prev = out[-1] if out else None
        if prev and prev[0] == cur[0] and cur[2] < prev[3]:  # overlap: keep the CONTAINED span (the value proper)
            if (cur[3] - cur[2]) < (prev[3] - prev[2]):
                out[-1] = cur
            continue
        if prev and prev[0] == cur[0] and flat[prev[3]:cur[2]].strip() in ("(", ")", ") ("):  # restated in parentheses
            if not any(ch.isdigit() for ch in prev[1]) and any(ch.isdigit() for ch in cur[1]):
                out[-1] = cur
            continue
        out.append(cur)
    return out


def residual_request(text: str, cands: list[tuple[str, str]]) -> tuple[str, dict]:
    """The decision-model request for one provision: the rubric (ttl), the provision (whitespace flattened), then
    the numbered candidates; one `choice` per candidate among the ttl's residual roles."""
    from rag_wright.ontology.loader import load_residual_role_criteria, load_residual_role_rubric

    body = "\n".join(f"[{i}] {s}" for i, (_k, s) in enumerate(cands))
    state = f"{load_residual_role_rubric()}\n\nProvision:\n{' '.join(text.split())[:6000]}\n\nCandidates:\n{body}"
    roles = load_residual_role_criteria()
    return state, {f"c{i}": {"type": "choice", "instructions": f"Candidate [{i}]", "criteria": dict(roles)}
                   for i in range(len(cands))}


Decide = Callable[[dict], Awaitable[dict]]


class DecisionResidualExtractor:
    """The residual lane on a decision model: `aextract_values(text) -> [(dimension, value, probability)]`. No
    candidates -> no call; a decision error -> no values (never a guessed value)."""

    def __init__(self, decide: Decide) -> None:
        self._decide = decide

    async def aextract_values(self, text: str) -> list[tuple[str, str, float]]:
        cands = candidates(text)
        if not cands:
            return []
        state, questions = residual_request(text, cands)
        try:
            out = await self._decide({"state": state, "questions": questions})
            answers = out.get("answers", {}) if isinstance(out, dict) else {}
        except Exception:  # noqa: BLE001 - a decision-model blip yields no residual values, never a failure
            return []
        flat = " ".join(text.split())
        picked = []
        for i, (_k, span, start, end) in enumerate(_positioned(text)):
            a = answers.get(f"c{i}") or {}
            role = a.get("choice")
            if role and role != "none":
                picked.append((role, span, start, end, float((a.get("probabilities") or {}).get(role, 0.0))))
        return [(role, clean_value(span, role), p) for role, span, _s, _e, p in _one_per_value(picked, flat)]

    def extract_values(self, text: str) -> list[tuple[str, str, float]]:
        """Sync twin (no running event loop in the caller)."""
        return asyncio.run(self.aextract_values(text))


def select_residual_extractor(resources: Any = None) -> Optional[DecisionResidualExtractor]:
    """The decision-model residual lane when a decision model is configured; None (-> the LLM residual call) when
    none is, or when `RAG_RESIDUAL_EXTRACTOR=llm`."""
    from rag_wright.api import ainvoke_model, capability_index
    from rag_wright.models.profiles import decision_profile

    if os.environ.get("RAG_RESIDUAL_EXTRACTOR", "decision") == "llm":
        return None
    model = os.environ.get("RAG_DECISION_MODEL")
    if not os.environ.get(decision_profile(model).api_key_env) or "jev_decision" not in capability_index():
        return None

    async def decide(inputs: dict) -> dict:
        return await ainvoke_model("jev_decision", {**inputs, **({"model": model} if model else {})}, resources=resources)

    return DecisionResidualExtractor(decide)
