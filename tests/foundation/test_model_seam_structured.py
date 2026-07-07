"""A-T2 (T12): forced structured output on the structured-reasoning models, live via the seam.

Proves the risky seam before extraction (T23) depends on it (plan A-T2, risk 3): a forced-schema
call returns a valid contract instance *through the model-profile seam*, for both prioritized
structured-reasoning models. DeepSeek V4 Pro (primary) honors the forced tool call while reasoning;
Qwen 3.7 Plus (secondary) only succeeds because its profile disables thinking on the forced
structured call alone (it otherwise returns "<400> ... tool_choice ... in thinking mode"). The
working profiles are recorded in ADR-0006.

Opt-in and live: marked `model`, so it is skipped unless run with `-m model` (needs OpenRouter
credentials in `.env`). See `conftest.py`.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from rag_wright.models import seam
from rag_wright.models.profiles import ModelRole, model_for, profile_for

pytestmark = pytest.mark.model


class _PartyExtraction(BaseModel):
    """A minimal extraction target: one contracting party and its role."""

    name: str = Field(description="the legal name of the party")
    role: str = Field(description="the party's role, e.g. Licensor or Licensee")


_PROMPT = (
    "From this sentence, extract the party and its role: "
    "'This Agreement is entered into by Acme Corporation (the \"Licensor\").'"
)

_ROLES = [ModelRole.STRUCTURED_REASONING, ModelRole.STRUCTURED_REASONING_SECONDARY]


@pytest.mark.parametrize("role", _ROLES, ids=[r.value for r in _ROLES])
def test_forced_schema_call_returns_valid_contract_through_the_seam(role: ModelRole):
    model_id = model_for(role)
    # the profile is registered (not the unknown-model fallback) and drives this call
    assert profile_for(model_id).model_id == model_id

    result = seam.build_structured(model_id, _PartyExtraction).invoke(_PROMPT)

    assert isinstance(result, _PartyExtraction)  # a valid contract instance, validated by Pydantic
    assert "acme" in result.name.lower()
    assert result.role.strip()
