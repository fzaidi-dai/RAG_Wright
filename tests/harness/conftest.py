"""The harness's hermetic tests do not route through the decision model unless they ask to (ADR-0146).

The root conftest registers the reference pack, which brings the decision model (`jev_decision`) into the catalog, and
with a key in `.env` the chat-manager's router would really call it -- reaching the network from a hermetic test. So
here it is not in the catalog, and the router steps aside as it does for any product that never registers it. A test
that wants the router patches the catalog itself; a live test (`model`) keeps the real one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_decision_model_unless_asked(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("model"):
        return
    import rag_wright.api

    catalog = rag_wright.api.capability_index
    monkeypatch.setattr(rag_wright.api, "capability_index",
                        lambda: {slug: card for slug, card in catalog().items() if slug != "jev_decision"})
