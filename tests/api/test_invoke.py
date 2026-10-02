"""EP-API-2 (ADR-0117): the capability invoker (an ARD client over the metadata catalog). Hermetic tests cover the
light index, name/kind validation, the drift guard (every wired adapter is in the ARD catalog with a matching kind),
and that the invoker opens usage accounting around the call. The live tests invoke a real capability through the
engine API -- the local LegalBERT model, and (store-gated) a real subgraph over a workspace."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from rag_wright.api import EngineConfig, StoreConfig, WorkspaceHandle, ainvoke_subgraph, capability_index, invoke_model
from rag_wright.api import invoke as _invoke
from rag_wright.models.usage import usage_capturing


def _handle():
    return WorkspaceHandle(store=object(), config=EngineConfig(store=StoreConfig(host="h", port="1", user="u", password="p")), corpus="c")


# --- light index + validation + drift guard ---

def test_capability_index_is_built_from_the_ard_specs():
    idx = capability_index()
    assert idx["typed_property_retrieval"]["kind"] == "subgraph"
    assert idx["clause_function_classification"]["kind"] == "model"
    assert idx["typed_property_retrieval"]["description"]  # the 'card' carries a description


def test_every_wired_adapter_is_in_the_catalog_with_matching_kind():
    """Drift guard: the client's adapter binding must not diverge from the ARD catalog."""
    idx = capability_index()
    for name, kind in _invoke._invocable_names().items():
        assert name in idx, f"adapter {name!r} not in the ARD catalog"
        assert idx[name]["kind"] == kind, f"adapter {name!r} kind {kind!r} != catalog {idx[name]['kind']!r}"


async def test_ainvoke_subgraph_rejects_unknown_name_and_wrong_kind():
    with pytest.raises(KeyError):
        await ainvoke_subgraph("not_a_capability", {}, resources=_handle())
    with pytest.raises(ValueError):  # a model capability invoked via the subgraph invoker
        await ainvoke_subgraph("clause_function_classification", {}, resources=_handle())


def test_invoke_model_reports_missing_adapter_clearly():
    with pytest.raises(NotImplementedError):
        invoke_model("embedding", {}, resources=_handle())  # 'embedding' is a model kind with no adapter wired yet


async def test_ainvoke_subgraph_dispatches_under_usage_accounting(monkeypatch):
    seen = {}

    async def _stub(handle, inputs):
        seen["usage_open"] = usage_capturing()   # the invoker opened a usage scope around the call
        seen["inputs"] = inputs
        return {"ok": True}

    # 'relational_qa' is a real subgraph slug in the catalog with no adapter wired -> inject a stub
    monkeypatch.setitem(_invoke._SUBGRAPH_ADAPTERS, "relational_qa", _stub)
    out = await ainvoke_subgraph("relational_qa", {"q": 1}, resources=_handle())
    assert out == {"ok": True} and seen["usage_open"] is True and seen["inputs"] == {"q": 1}


# --- live: a local model capability through the engine API ---

_SETFIT_ROOT = Path(os.getenv("RAG_SETFIT_CLAUSE_DIR", "data/models/setfit_clause"))
_HAVE_SETFIT = (_SETFIT_ROOT / "cap128b_legalbert" / "model_head.pkl").exists()


@pytest.mark.skipif(not _HAVE_SETFIT, reason="SetFit clause checkpoints not present (gitignored / local-only)")
def test_invoke_model_runs_the_real_clause_function_classifier():
    out = invoke_model("clause_function_classification",
                       {"chunk_text": "Section 8. Limitation of Liability.",
                        "span_texts": ["In no event shall either party's aggregate liability exceed the fees paid."]},
                       resources=_handle())
    assert isinstance(out, list) and len(out) == 1          # one result row per span
    assert isinstance(out[0], list)                          # soft tags (FunctionScore list) for the span


# --- live: a real subgraph over a workspace (needs ArcadeDB + the model backend) ---

@pytest.mark.store
def test_ainvoke_subgraph_runs_typed_property_retrieval_live():
    cfg = EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))
    from rag_wright.api import open_workspace

    ws = open_workspace(cfg, corpus="ragwright_invoke_live", reset=True)
    import asyncio

    out = asyncio.run(ainvoke_subgraph("typed_property_retrieval", {"query": "liability cap"}, resources=ws))
    assert out is not None and "retrieval" in out  # the leg ran end-to-end through the engine API (empty KG -> no spans)
