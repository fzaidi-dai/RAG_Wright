"""T56 (FR-R, ADR-0025): CUAD gold -> operative-span function labels.

CUAD (`CUAD_v1.json`, SQuAD-style) annotates each contract with answer spans per clause type. To train the
function classifier at the SAME granularity we infer on (operative spans, T55), we segment each CUAD contract's
text with the operative-span segmenter and label each span by the clause type of the CUAD answer span it
overlaps most (else NONE). Same segmenter, same unit, both sides -- no train/infer granularity mismatch.

The clause type is parsed from the CUAD question (`... related to "<Type>" ...`); types are the canonical CUAD
label names (== `ClauseCategory` values, ADR-0002). A span overlapping answers of two types takes the one with
the greater character overlap (single-label). The contract id is kept so callers can split train/test by
contract (no leakage across the split).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel

from rag_wright.packs.contracts.spans.function_classifier import NONE_LABEL
from rag_wright.packs.contracts.spans.segment import segment_clause

_TYPE_IN_QUESTION = re.compile(r'related to\s+"([^"]+)"')


class CuadAnswer(BaseModel):
    clause_type: str
    start: int  # char offset into the contract context
    text: str


class CuadContract(BaseModel):
    contract_id: str
    context: str  # the full contract text
    answers: list[CuadAnswer]


class LabeledSpan(BaseModel):
    contract_id: str
    text: str
    label: str  # a CUAD clause type, or NONE


def parse_cuad(path: Path) -> Iterator[CuadContract]:
    """Yield one `CuadContract` per CUAD contract (id, full context, its labeled answer spans)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for entry in payload["data"]:
        contract_id = str(entry.get("title") or entry.get("id") or "")
        for para in entry["paragraphs"]:
            context = para["context"]
            answers: list[CuadAnswer] = []
            for qa in para["qas"]:
                m = _TYPE_IN_QUESTION.search(qa.get("question", ""))
                if not m:
                    continue
                clause_type = m.group(1).strip()
                for ans in qa.get("answers", []):
                    text = str(ans["text"])
                    if text:
                        answers.append(CuadAnswer(clause_type=clause_type, start=int(ans["answer_start"]), text=text))
            yield CuadContract(contract_id=contract_id, context=context, answers=answers)


def label_operative_spans(contract: CuadContract) -> list[LabeledSpan]:
    """Segment the contract into operative spans; label each by the max-overlapping CUAD answer's type (else NONE)."""
    spans = segment_clause(contract.contract_id, contract.context)
    out: list[LabeledSpan] = []
    for s in spans:
        best_label, best_overlap = NONE_LABEL, 0
        for ans in contract.answers:
            a_end = ans.start + len(ans.text)
            overlap = min(s.end, a_end) - max(s.start, ans.start)
            if overlap > best_overlap:
                best_overlap, best_label = overlap, ans.clause_type
        text = s.text.strip()
        if text:
            out.append(LabeledSpan(contract_id=contract.contract_id, text=text, label=best_label))
    return out
