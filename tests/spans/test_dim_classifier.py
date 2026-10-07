"""CLS-A (FR-I.4): the DimClassifier seam — device selection, registry, and Protocol conformance. Hermetic (no
model downloads); the real-model fidelity check (SetFit + laya loaders on real checkpoints) is the live CLS-A gate."""

from __future__ import annotations

from rag_wright.packs.contracts.schemas.property import PropertyDimension as D
from rag_wright.packs.contracts.spans.dim_classifier import DimClassifier, DimClassifierRegistry, auto_device


class _StubDim:
    """A DimClassifier that returns a fixed ranking — exercises the seam without loading a real model."""

    def __init__(self, dim: D, ranking: list[tuple[str, float]]) -> None:
        self.dim = dim
        self._ranking = ranking

    def classify(self, span_text: str) -> list[tuple[str, float]]:  # noqa: ARG002
        return self._ranking


def test_auto_device_prefers_explicit_then_falls_back():
    assert auto_device("cpu") == "cpu"                     # explicit choice always honored
    assert auto_device("cuda") == "cuda"
    assert auto_device(None) in ("cuda", "mps", "cpu")     # else GPU-if-available-else-CPU, never pinned/erroring


def test_stub_satisfies_dimclassifier_protocol():
    stub = _StubDim(D.IP_OWNERSHIP, [("assigned", 0.9)])
    assert isinstance(stub, DimClassifier)                 # runtime_checkable Protocol conformance


def test_registry_get_covers_dims_and_falls_back_to_none():
    reg = DimClassifierRegistry({
        D.IP_OWNERSHIP: _StubDim(D.IP_OWNERSHIP, [("assigned", 0.8), ("joint", 0.15)]),
        D.TERMINATION_RIGHT: _StubDim(D.TERMINATION_RIGHT, [("either_party", 0.7)]),
    })
    assert reg.covers(D.IP_OWNERSHIP) and reg.covers(D.TERMINATION_RIGHT)
    assert not reg.covers(D.FAVORABILITY)                  # uncovered dim -> LLM fallback in CLS-B
    assert reg.get(D.FAVORABILITY) is None
    assert set(reg.dims) == {D.IP_OWNERSHIP, D.TERMINATION_RIGHT}
    # a covered dim returns ranked (value, prob), highest first
    top = reg.get(D.IP_OWNERSHIP).classify("… assigns all right, title and interest …")
    assert top[0] == ("assigned", 0.8) and top[0][1] >= top[1][1]
