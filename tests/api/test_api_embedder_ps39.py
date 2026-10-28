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


def test_concurrent_first_use_builds_one_embedder(monkeypatch):
    # PS-52 (TexWright, 2026-10-24): the lazy build was an unguarded check-then-set, so N threads arriving before the
    # first build finished built N models (four "Loading weights" for one workspace, then a SIGSEGV).
    import concurrent.futures as cf
    import threading
    import time

    from rag_wright.api import workspace as w

    builds = []

    def slow_build(cfg):
        builds.append(1)
        time.sleep(0.2)  # a model load: long enough for every thread to arrive before it finishes
        return object()

    monkeypatch.setattr(w, "_build_embedder", slow_build)
    config = api.EngineConfig(store=api.StoreConfig(host="h", port="1", user="u", password="p"))
    ws = w.WorkspaceHandle(store=object(), config=config, corpus="c")
    start = threading.Barrier(8)

    def first_use(_):
        start.wait()
        return ws.embedder

    with cf.ThreadPoolExecutor(8) as ex:
        got = list(ex.map(first_use, range(8)))
    assert len(builds) == 1
    assert len({id(e) for e in got}) == 1
