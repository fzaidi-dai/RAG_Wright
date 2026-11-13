"""The harness's hermetic tests do not call the decision model unless they ask to (ADR-0146).

The harness registers and calls its decision model itself, and with a key in `.env` it would really call it --
reaching the network from a hermetic test. So here the decision model's call fails, and the decider routes as
before, as it does for a deployment without the decision model's credentials. A test that wants the router patches
`ainvoke_model` itself; a live test (`model`) keeps the real one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_decision_model_unless_asked(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("model"):
        return
    import rag_wright.api

    invoke = rag_wright.api.ainvoke_model

    async def no_decision_model(name: str, inputs: dict, **kwargs: object) -> object:
        if name == "jev_decision":
            raise RuntimeError("the decision model is not called in a hermetic harness test")
        return await invoke(name, inputs, **kwargs)

    monkeypatch.setattr(rag_wright.api, "ainvoke_model", no_decision_model)
