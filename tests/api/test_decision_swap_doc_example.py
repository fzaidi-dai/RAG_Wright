"""PS-11: the "Swapping the decision behind a hook" example in the classification & decision models guide runs as
written. Its code blocks are executed in order (Jev stubbed, a fake classifier supplied), then the tagger they build is
called through the real invoker, before and after the slug is re-registered, and the voting representative is applied
to its output."""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import rag_wright.capabilities.jev_decision as jev_module
from rag_wright.api import Span
from rag_wright.capabilities import manifests as m

_GUIDE = Path(__file__).resolve().parents[2] / "docs" / "domain-adaptation" / "classification-and-decision-models.md"
_HEADING = "## 5. Swapping the decision behind a hook"


def _blocks() -> list[str]:
    text = _GUIDE.read_text(encoding="utf-8")
    section = text[text.index(_HEADING):]
    section = section[: section.index("\n## ", len(_HEADING))]
    return re.findall(r"```python\n(.*?)```", section, re.S)


@pytest.fixture
def catalog():
    saved = dict(m.MANIFEST_SPECS)
    m.MANIFEST_SPECS.clear()
    try:
        yield
    finally:
        m.MANIFEST_SPECS.clear()
        m.MANIFEST_SPECS.update(saved)


class _FakeClassifier:
    def predict_proba(self, texts):
        return [{"test_result": 0.9, "batch_id": 0.1} for _ in texts]


async def _fake_jev(resources, inputs):  # noqa: ARG001
    items = dict(re.findall(r"^\[(\d+)\] (.*)$", inputs["state"], re.M))
    answers = {}
    for qid in inputs["questions"]:
        hit = "batch" in items[qid[1:]].lower()
        choice = "batch_id" if hit else "none"
        probs = {"batch_id": 0.8, "test_result": 0.1, "none": 0.1} if hit else \
            {"batch_id": 0.05, "test_result": 0.05, "none": 0.9}
        answers[qid] = {"type": "choice", "choice": choice, "probabilities": probs}
    return {"answers": answers, "usage": {}}


def _span(i: int, text: str) -> Span:
    return Span(span_id=f"c#{i}", parent_chunk_id="c", span_index=i, start=0, end=len(text), text=text)


async def test_the_guide_example_swaps_the_model_by_slug(catalog, monkeypatch):
    blocks = _blocks()
    assert len(blocks) >= 4
    monkeypatch.setattr(jev_module, "jev_decision", _fake_jev)
    module = types.ModuleType("my_product.caps.labelling")
    for name in ("my_product", "my_product.caps"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "my_product.caps.labelling", module)

    ws = SimpleNamespace()
    ns = {"ws": ws, "my_classifier": _FakeClassifier(), "my_extractor": lambda unit: None}
    for block in blocks:
        exec(compile(block, str(_GUIDE), "exec"), ns)  # noqa: S102 - the guide's own example, verbatim
        for impl in ("classifier_span_labelling", "jev_span_labelling"):
            if impl in ns:
                setattr(module, impl, ns[impl])

    # the example's last registration is the Jev arm, under the same slug
    assert m.MANIFEST_SPECS["span_labelling"].impl_ref.endswith(":jev_span_labelling")
    spans = [_span(0, "Batch B-17 was dyed on 3 May."), _span(1, "The weather was fine.")]
    tagged = await ns["make_span_tagger"](ws)("chunk text", spans)
    assert [t.tags for t in tagged] == [["batch_id"], []]
    assert tagged[0].scores["batch_id"] == 0.8 and "none" not in tagged[0].scores
    rep = ns["vote"](tagged)
    assert rep.span.span_id == "c#0" and rep.primary_tag == "batch_id"

    # swap back: the same slug, the classifier's impl_ref; the tagger is unchanged
    ns["register_span_labelling"]("classifier_span_labelling")
    tagged = await ns["make_span_tagger"](ws)("chunk text", spans)
    assert [t.primary_tag for t in tagged] == ["test_result", "test_result"]
    assert "pipeline" in ns
