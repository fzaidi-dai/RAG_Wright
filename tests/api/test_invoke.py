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
    assert idx["clause_property_classification"]["kind"] == "model"  # EP-RT-1: the 29-dim fleet as a model capability
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


def test_invoke_model_dispatches_clause_property_classification_under_usage(monkeypatch):
    """The model invoker routes the slug to its adapter and opens a usage scope (hermetic: stub the adapter so no
    heavy fleet load is needed)."""
    seen = {}

    def _stub(handle, inputs):
        seen["usage_open"] = usage_capturing()
        seen["inputs"] = inputs
        return [{"dimension": "liability_cap_basis", "value": "FEES_PAID", "confidence": "EXTRACTED"}]

    monkeypatch.setitem(_invoke._MODEL_ADAPTERS, "clause_property_classification", _stub)
    out = invoke_model("clause_property_classification", {"text": "liability cap", "functions": ("Cap",)},
                       resources=_handle())
    assert out[0]["dimension"] == "liability_cap_basis"
    assert seen["usage_open"] is True and seen["inputs"] == {"text": "liability cap", "functions": ("Cap",)}


def test_source_document_builds_a_text_doc():
    from rag_wright.api import source_document

    sd = source_document("ACME_MSA", text="Section 8. Limitation of Liability.")
    assert sd.source_doc_id == "ACME_MSA" and "Limitation of Liability" in sd.text


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


# --- live: the 29-dim property-classifier fleet as a model capability through the engine API ---

def _fleet_present() -> bool:
    """True only when every model dir the fleet references has been fetched (gitignored / local-only)."""
    import json

    cfg_path = Path("src/rag_wright/spans/dim_fleet.json")
    models_dir = Path(os.getenv("RAG_DIM_MODELS_DIR", "data/models"))
    try:
        cfg = json.loads(cfg_path.read_text())
    except OSError:
        return False
    for spec in cfg.values():
        sub = "laya" if spec["framework"] == "laya" else "setfit"
        if not (models_dir / sub / spec["model"]).exists():
            return False
    return True


@pytest.mark.skipif(not _fleet_present(), reason="29-dim fleet checkpoints not present (gitignored / local-only)")
def test_invoke_model_runs_the_real_clause_property_classifier():
    """EP-RT-1: the classifier lane, invoked as a `model` capability through the engine API, returns real soft tags
    for a provision. A dispute-resolution clause (soft-scoped to its function) must fire dispute_method non-abstain."""
    out = invoke_model(
        "clause_property_classification",
        {"text": ("Any dispute, controversy or claim arising out of or relating to this Agreement shall be finally "
                  "settled by binding arbitration administered by the American Arbitration Association under its "
                  "Commercial Arbitration Rules."),
         "functions": ("Dispute Resolution",)},
        resources=_handle())
    assert isinstance(out, list) and out, "the fleet emitted no soft tags for a dispute-resolution clause"
    assert all(set(t) == {"dimension", "value", "confidence"} for t in out)
    dispute = [t for t in out if t["dimension"] == "dispute_method"]
    assert dispute and all(str(t["value"]).lower() != "none" for t in dispute), (
        f"dispute_method did not fire (got {dispute}); emitted={[(t['dimension'], t['value']) for t in out]}")


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


@pytest.mark.store
def test_ainvoke_intra_document_qa_live():
    """intra_document_qa over an empty workspace: serve finds no clauses -> generate abstains (no model call needed),
    proving the adapter wires the leg end-to-end through the API."""
    import asyncio

    from rag_wright.api import open_workspace

    cfg = EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))
    ws = open_workspace(cfg, corpus="ragwright_invoke_idqa_live", reset=True)
    out = asyncio.run(ainvoke_subgraph(
        "intra_document_qa", {"contract_id": "no-such-doc", "question": "What is the liability cap?"}, resources=ws))
    assert out is not None  # the leg ran; empty KG -> an honest abstain


@pytest.mark.store
def test_ainvoke_relational_qa_live():
    import asyncio

    from rag_wright.api import open_workspace

    cfg = EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))
    ws = open_workspace(cfg, corpus="ragwright_invoke_relqa_live", reset=True)
    out = asyncio.run(ainvoke_subgraph(
        "relational_qa", {"query": "who does Acme contract with?", "start_entity_id": "none", "max_hops": 1},
        resources=ws))
    assert out is not None  # empty graph -> empty traversal -> abstain; the leg ran through the API
