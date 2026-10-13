"""PS-37, live against ArcadeDB: a temporary workspace exists inside its block and is gone after it."""
from __future__ import annotations

import os

import pytest

from rag_wright.api import EngineConfig, StoreConfig, drop_workspace, open_workspace, temporary_workspace


@pytest.mark.store
def test_live_temporary_workspace_is_created_then_dropped():
    from rag_wright.store.seam import KgNode

    cfg = EngineConfig(store=StoreConfig(
        host=os.environ["ARCADEDB_HOST"], port=os.environ["ARCADEDB_PORT"],
        user=os.environ["ARCADEDB_USER"], password=os.environ["ARCADEDB_PASSWORD"],
        protocol=os.getenv("ARCADEDB_PROTOCOL", "http")))

    with temporary_workspace(cfg, prefix="ragwright_tmp") as ws:
        name = ws._corpus
        ws._store.kg_write([KgNode("Entity", "entity_id", {"entity_id": "e1", "name": "x"})])
        assert open_workspace(cfg, corpus=name, create=False) is ws   # it exists, and it is the cached handle
    with pytest.raises(LookupError):
        open_workspace(cfg, corpus=name, create=False)               # dropped on exit
    assert drop_workspace(cfg, corpus=name) is False                 # nothing left to drop
