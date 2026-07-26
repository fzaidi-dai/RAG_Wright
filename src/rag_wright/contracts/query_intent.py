"""The NL->type query-understanding output contract (CU-A1 / CU-C1, ADR-0029).

The front door of the CUAD pipeline: a natural-language question about a known contract is parsed (one LLM
call) into this structured intent. `clause_types` are validated to the retrieval FUNCTION taxonomy
(`FUNCTION_LABELS`), normalized case-insensitively at the boundary. `intent` routes the serve stage:
- highlight  -> return the clause's spans as-is
- extract    -> also field-extract `value_to_extract` from the matched clause (value-type categories)
- discriminate -> `value_condition` selects among same-type clauses (case (b): detected now, stage stubbed)
`in_taxonomy=False` marks an out-of-taxonomy query -> serve falls back to semantic span search + a
low-confidence flag. Multi-type is allowed (`clause_types` is a list).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from rag_wright.contracts.function import canonical_function

Intent = Literal["highlight", "extract", "discriminate"]


class QueryIntent(BaseModel):
    """Structured NL->type intent produced by query understanding (CU-C1)."""

    model_config = {"frozen": True}

    clause_types: list[str] = []  # subset of FUNCTION_LABELS (canonicalized); empty iff out-of-taxonomy
    intent: Intent = "highlight"
    value_to_extract: str | None = None  # for intent=extract: which value to pull from the clause
    value_condition: str | None = None  # for intent=discriminate: the value condition selecting the clause
    in_taxonomy: bool = True  # False -> semantic fallback + low-confidence flag at serve
    confidence: float = 1.0  # 0..1 query-understanding confidence

    @field_validator("clause_types")
    @classmethod
    def _canonicalize_types(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for label in v:
            canon = canonical_function(label)
            if canon is None:
                raise ValueError(f"clause_type {label!r} is not in the FUNCTION taxonomy")
            if canon not in out:
                out.append(canon)
        return out

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return v
