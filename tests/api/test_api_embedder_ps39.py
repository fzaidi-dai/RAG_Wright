"""PS-39: a product's seam embeds queries through `rag_wright.api` alone (it may not import `rag_wright.pack_sdk`):
the read-only `WorkspaceHandle.embedder`, and `query_embedder()` for code that holds no workspace."""
import pytest

from rag_wright import api, pack_sdk


def _handle(monkeypatch, sentinel):
    from rag_wright.api import workspace as w
    monkeypatch.setattr(w, "_build_embedder", lambda cfg: sentinel)
    config = api.EngineConfig(store=api.StoreConfig(host="h", port="1", user="u", password="p"))
    return w.WorkspaceHandle(store=object(), config=config, corpus="c")


def test_the_workspace_embedder_is_a_read_only_property_from_the_profile(monkeypatch):
    sentinel = object()
    ws = _handle(monkeypatch, sentinel)
    assert ws.embedder is sentinel
    assert pack_sdk.workspace_embedder(ws) is ws.embedder  # the pack tier's twin is the same embedder
    with pytest.raises(AttributeError):
        ws.embedder = object()


def test_query_embedder_is_on_the_api_and_is_the_pack_sdk_function():
    assert "query_embedder" in api.__all__
    assert api.query_embedder is pack_sdk.query_embedder  # still importable from the pack tier (not in its __all__)


def test_the_workspace_handle_names_embedder_in_its_public_surface():
    public = sorted(n for n in dir(api.WorkspaceHandle) if not n.startswith("_"))
    assert public == ["config", "embedder", "model_id"]
