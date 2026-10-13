"""PS-37: the public surface can remove a database it created. `drop_workspace` drops a named corpus (never a
default one) and forgets its cached handle; `temporary_workspace` opens a fresh, uniquely named corpus and drops it
on exit, whether the block succeeds or raises, so a measurement's scratch database cannot outlive the measurement."""
from __future__ import annotations

import pytest

from rag_wright.api import EngineConfig, StoreConfig, drop_workspace, open_workspace, temporary_workspace
from rag_wright.api import workspace as w

CONFIG = EngineConfig(store=StoreConfig(host="h", port="1", user="u", password="p"))


class _Store:
    def __init__(self, corpus):
        self.corpus, self.closed = corpus, False

    def ensure_schema(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def server(monkeypatch):
    """A fake store server: the databases that exist, and every store built."""
    state = {"databases": set(), "stores": [], "drops": []}

    def build_store(config, corpus, *, reset, create=True):
        state["databases"].add(corpus)
        state["stores"].append(_Store(corpus))
        return state["stores"][-1]

    def drop_database(config, corpus):
        state["drops"].append(corpus)
        existed = corpus in state["databases"]
        state["databases"].discard(corpus)
        return existed

    monkeypatch.setattr(w, "_build_store", build_store)
    monkeypatch.setattr(w, "_drop_database", drop_database)
    monkeypatch.setattr(w, "_WORKSPACES", {})
    return state


@pytest.mark.parametrize("corpus", ["", "   ", None])
def test_drop_refuses_a_missing_or_blank_name(server, corpus):
    # an empty name would otherwise fall through to the default database
    with pytest.raises(ValueError):
        drop_workspace(CONFIG, corpus=corpus)
    assert server["drops"] == []


def test_drop_reports_whether_the_database_existed(server):
    open_workspace(CONFIG, corpus="scratch")
    assert drop_workspace(CONFIG, corpus="scratch") is True
    assert "scratch" not in server["databases"]
    assert drop_workspace(CONFIG, corpus="scratch") is False


def test_drop_forgets_and_closes_the_cached_handle(server):
    first = open_workspace(CONFIG, corpus="scratch")
    drop_workspace(CONFIG, corpus="scratch")
    assert server["stores"][0].closed
    second = open_workspace(CONFIG, corpus="scratch")
    assert second is not first and len(server["stores"]) == 2


def test_a_temporary_workspace_is_dropped_on_exit(server):
    with temporary_workspace(CONFIG, prefix="probe") as ws:
        name = ws._corpus
        assert name.startswith("probe_") and name in server["databases"]
    assert name not in server["databases"] and server["drops"] == [name]


def test_a_temporary_workspace_is_dropped_when_the_block_raises(server):
    with pytest.raises(RuntimeError, match="measurement failed"):
        with temporary_workspace(CONFIG) as ws:
            name = ws._corpus
            raise RuntimeError("measurement failed")
    assert name.startswith("tmp_") and name not in server["databases"]


def test_temporary_workspaces_get_distinct_names(server):
    with temporary_workspace(CONFIG) as a, temporary_workspace(CONFIG) as b:
        assert a._corpus != b._corpus


def test_a_temporary_workspace_refuses_a_blank_prefix(server):
    with pytest.raises(ValueError):
        with temporary_workspace(CONFIG, prefix=" "):
            pass
    assert server["databases"] == set()

