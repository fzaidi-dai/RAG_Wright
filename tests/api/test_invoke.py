"""EP-API-2 (ADR-0117): the capability invoker (an ARD client over the metadata catalog). Hermetic tests cover the
light index, name/kind validation, the drift guard (every wired adapter is in the ARD catalog with a matching kind),
and that the invoker opens usage accounting around the call. The live tests invoke a real capability through the
engine API -- the local LegalBERT model, and (store-gated) a real subgraph over a workspace."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import asyncio
import threading

from rag_wright.api import (
    EngineConfig,
    StoreConfig,
    WorkspaceHandle,
    ainvoke_model,
    ainvoke_subgraph,
    capability_index,
    invoke_model,
)
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


def test_invoke_model_rejects_a_de_registered_or_unknown_capability():
    # 'embedding' was de-registered from ARD (EP-CORE-1a/ADR-0118 — it's a core primitive now, not a capability),
    # so it is no longer invocable by name: the catalog lookup fails.
    with pytest.raises(KeyError):
        invoke_model("embedding", {}, resources=_handle())


async def test_ainvoke_subgraph_dispatches_the_inputs(monkeypatch):
    seen = {}

    async def _stub(resources, inputs):
        seen["inputs"] = inputs
        return {"ok": True}

    # EP-CORE-2: the invoker resolves the impl via `capability_impl` (no central adapter dict) -> stub it.
    # 'relational_qa' is a real subgraph slug in the catalog (validated), so _validate passes.
    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _stub)
    out = await ainvoke_subgraph("relational_qa", {"q": 1}, resources=_handle())
    assert out == {"ok": True} and seen["inputs"] == {"q": 1}
    # usage is the CALLER's concern now (EP-API-5): the invoker opens no scope of its own
    assert not usage_capturing()


def test_invoke_model_dispatches_through_the_single_model_binding(monkeypatch):
    """invoke_model validates against the ARD catalog then resolves the impl via `capability_impl` (the impl_ref
    path, EP-CORE-2) -- the same resolution the ingestion pipeline's dispatch uses. Hermetic: stub the resolver so
    no heavy fleet loads. Model factories take `(resources, inputs)`."""
    seen = {}

    def _stub(resources, inputs):
        seen["inputs"] = inputs
        return [{"dimension": "liability_cap_basis", "value": "FEES_PAID", "confidence": "EXTRACTED"}]

    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _stub)
    out = invoke_model("clause_property_classification", {"text": "liability cap", "functions": ("Cap",)},
                       resources=_handle())
    assert out[0]["dimension"] == "liability_cap_basis"
    assert seen["inputs"] == {"text": "liability cap", "functions": ("Cap",)}


# --- EP-API-7: async model invocation on the API (`ainvoke_model`) ---

async def test_ainvoke_model_runs_a_sync_impl_off_the_event_loop(monkeypatch):
    """A SYNC model impl (CPU-bound local inference, e.g. a classifier/XGBoost fleet) must run OFF the event loop
    (in a worker thread), so a big batch never blocks the loop. Proven by the impl observing a different thread id
    than the caller's loop thread."""
    loop_thread = threading.get_ident()
    seen = {}

    def _sync_impl(resources, inputs):
        seen["thread"] = threading.get_ident()
        seen["inputs"] = inputs
        return [{"dimension": "x", "value": "y", "confidence": "EXTRACTED"}]

    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _sync_impl)
    out = await ainvoke_model("clause_property_classification", {"text": "t", "functions": ()}, resources=_handle())
    assert out[0]["dimension"] == "x" and seen["inputs"] == {"text": "t", "functions": ()}
    assert seen["thread"] != loop_thread  # ran in a worker thread, not on the event loop


async def test_ainvoke_model_awaits_an_async_impl_directly(monkeypatch):
    """An ASYNC model impl (I/O-bound, e.g. an LLM-backed cap calling OpenRouter / a local vLLM client) must be
    AWAITED directly, NOT shoved through a thread -- so it runs on the event loop thread (real async I/O)."""
    loop_thread = threading.get_ident()
    seen = {}

    async def _async_impl(resources, inputs):
        seen["thread"] = threading.get_ident()
        return {"answer": inputs["q"]}

    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _async_impl)
    out = await ainvoke_model("clause_function_classification", {"q": 42}, resources=_handle())
    assert out == {"answer": 42}
    assert seen["thread"] == loop_thread  # awaited on the loop, not off-loaded to a thread


async def test_ainvoke_model_bounds_fan_out_with_a_semaphore(monkeypatch):
    """`sem` bounds concurrent in-flight model calls -- the fan-out backpressure a product needs for a batch of
    LLM-backed model-cap calls. A Semaphore(2) must cap the observed concurrency at 2 regardless of timing."""
    live = 0
    peak = 0

    async def _async_impl(resources, inputs):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return inputs["i"]

    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _async_impl)
    sem = asyncio.Semaphore(2)
    outs = await asyncio.gather(*(
        ainvoke_model("clause_function_classification", {"i": i}, resources=_handle(), sem=sem) for i in range(6)))
    assert outs == [0, 1, 2, 3, 4, 5]  # order preserved by gather
    assert peak <= 2  # the semaphore held the ceiling


async def test_ainvoke_model_rejects_unknown_name_and_wrong_kind():
    with pytest.raises(KeyError):
        await ainvoke_model("not_a_capability", {}, resources=_handle())
    with pytest.raises(ValueError):  # a subgraph capability invoked via the model invoker
        await ainvoke_model("typed_property_retrieval", {}, resources=_handle())


def test_invoke_model_rejects_an_async_impl(monkeypatch):
    """The sync `invoke_model` must not silently return a coroutine for an async impl -- it raises a clear redirect
    to `ainvoke_model` (no silent-coroutine footgun)."""
    async def _async_impl(resources, inputs):
        return {"ok": True}

    monkeypatch.setattr(_invoke, "capability_impl", lambda name: _async_impl)
    with pytest.raises(TypeError, match="ainvoke_model"):
        invoke_model("clause_function_classification", {}, resources=_handle())


def test_source_document_builds_a_text_doc():
    from rag_wright.api import source_document

    sd = source_document("ACME_MSA", text="Section 8. Limitation of Liability.")
    assert sd.source_doc_id == "ACME_MSA" and "Limitation of Liability" in sd.text


# --- live: a local model capability through the engine API ---

_SETFIT_ROOT = Path(os.getenv("RAG_SETFIT_CLAUSE_DIR", "data/models/setfit_clause"))
_HAVE_SETFIT = (_SETFIT_ROOT / "cap128b_legalbert" / "model_head.pkl").exists()


@pytest.mark.fleet  # loads a LOCAL model (multi-GB RSS) -> opt-in, out of the default run
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

    cfg_path = Path("src/rag_wright/packs/contracts/spans/dim_fleet.json")
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


@pytest.mark.fleet  # loads the LOCAL 20-model property fleet (~4.6GB RSS) -> opt-in, out of the default run
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


@pytest.mark.fleet  # loads the LOCAL 20-model property fleet (~4.6GB RSS) -> opt-in, out of the default run
@pytest.mark.skipif(not _fleet_present(), reason="29-dim fleet checkpoints not present (gitignored / local-only)")
def test_ainvoke_model_runs_the_real_fleet_off_loop_with_fan_out():
    """EP-API-7 live: the real 29-dim fleet (a SYNC model impl) invoked through the ASYNC API surface
    `ainvoke_model` -- run off the event loop and fanned out over two provisions under a shared semaphore. Proves
    the async model surface works end-to-end on a real model cap with real backpressure."""
    clauses = [
        ("Any dispute arising out of this Agreement shall be finally settled by binding arbitration administered by "
         "the American Arbitration Association.", "Dispute Resolution", "dispute_method"),
        ("In no event shall either party's aggregate liability exceed the total fees paid in the prior twelve months.",
         "Cap On Liability", "cap_basis"),
    ]

    async def _drive():
        sem = asyncio.Semaphore(2)
        return await asyncio.gather(*(
            ainvoke_model("clause_property_classification", {"text": txt, "functions": (fn,)},
                          resources=_handle(), sem=sem)
            for txt, fn, _dim in clauses))

    outs = asyncio.run(_drive())
    assert len(outs) == 2 and all(isinstance(o, list) and o for o in outs)  # both provisions produced soft tags
    for (txt, fn, dim), tags in zip(clauses, outs):
        fired = [t for t in tags if t["dimension"] == dim and str(t["value"]).lower() != "none"]
        assert fired, f"{dim} did not fire for function {fn!r} (emitted={[(t['dimension'], t['value']) for t in tags]})"


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
