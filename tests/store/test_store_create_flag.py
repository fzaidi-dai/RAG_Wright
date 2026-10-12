"""PS-27: opening a store on a database that does not exist creates it (the default: a product provisions a new
corpus this way) -- visibly, in a log line -- and `create=False` refuses instead, naming what exists, for any path
where the database must already be there (a wrong name otherwise yields an empty graph, silently). Hermetic: the
ArcadeDB client is faked."""
from __future__ import annotations

import logging

import pytest

from rag_wright.store import arcadedb as adb


class _FakeDao:
    existing: set[str] = set()
    created: list[str] = []

    def __init__(self, client, database):
        self.database = database

    @classmethod
    def exists(cls, client, name):
        return name in cls.existing

    @classmethod
    def create(cls, client, name):
        cls.existing.add(name)
        cls.created.append(name)

    @classmethod
    def delete(cls, client, name):
        cls.existing.discard(name)

    @classmethod
    def list_databases(cls, client):
        return sorted(cls.existing)


@pytest.fixture(autouse=True)
def _fake(monkeypatch, request):
    if request.node.get_closest_marker("store"):
        return  # the live test uses the real client
    monkeypatch.setattr(adb, "DatabaseDao", _FakeDao)
    monkeypatch.setattr(adb, "SyncClient", lambda *a, **k: object())
    _FakeDao.existing = {"tenant_a", "tenant_b"}
    _FakeDao.created = []


def _open(**kw):
    return adb.ArcadeDBStore.from_config("localhost", "2480", "root", "pw", **kw)


def test_a_missing_database_is_created_by_default_and_the_creation_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger=adb.__name__):
        _open(database="tenant_new")
    assert _FakeDao.created == ["tenant_new"]
    assert "created database 'tenant_new' (it did not exist)" in caplog.text


def test_create_false_refuses_a_missing_database_and_names_what_exists():
    with pytest.raises(LookupError, match=r"'tenat_a' does not exist.*tenant_a, tenant_b"):
        _open(database="tenat_a", create=False)
    assert _FakeDao.created == []


def test_create_false_opens_an_existing_database_without_creating(caplog):
    with caplog.at_level(logging.INFO, logger=adb.__name__):
        _open(database="tenant_a", create=False)
    assert _FakeDao.created == [] and "created database" not in caplog.text


def test_reset_implies_create():
    with pytest.raises(ValueError, match="reset"):
        _open(database="tenant_a", reset=True, create=False)


def test_from_env_and_open_workspace_pass_create_through(monkeypatch):
    for k, v in {"ARCADEDB_HOST": "localhost", "ARCADEDB_PORT": "2480", "ARCADEDB_USER": "root",
                 "ARCADEDB_PASSWORD": "pw"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(LookupError):
        adb.ArcadeDBStore.from_env(database="nope", create=False)
    from rag_wright.api import EngineConfig, StoreConfig, open_workspace

    config = EngineConfig(store=StoreConfig(host="localhost", port="2480", user="root", password="pw"))
    with pytest.raises(LookupError, match="'nope_ws' does not exist"):
        open_workspace(config, corpus="nope_ws", create=False)
    assert _FakeDao.created == []


def test_the_reference_mcp_servers_never_create_a_corpus():
    from pathlib import Path

    root = Path(adb.__file__).resolve().parents[1] / "packs"
    servers = [p for p in root.rglob("*_server.py") if "open_workspace(" in p.read_text(encoding="utf-8")]
    assert len(servers) == 4
    assert all("create=False" in p.read_text(encoding="utf-8") for p in servers), [p.name for p in servers]


@pytest.mark.store
def test_live_arcadedb_refuses_with_create_false_and_creates_by_default(monkeypatch, caplog):
    import os

    from dotenv import load_dotenv

    real = adb
    load_dotenv(".env")
    name = "ps27_create_flag_live"
    from arcadedb_python import DatabaseDao, SyncClient

    client = SyncClient(os.environ["ARCADEDB_HOST"], os.environ["ARCADEDB_PORT"], username=os.environ["ARCADEDB_USER"],
                        password=os.environ["ARCADEDB_PASSWORD"])
    if DatabaseDao.exists(client, name):
        DatabaseDao.delete(client, name)
    with pytest.raises(LookupError, match=f"'{name}' does not exist"):
        real.ArcadeDBStore.from_env(database=name, create=False)
    assert not DatabaseDao.exists(client, name)  # refused, and nothing was created
    with caplog.at_level(logging.INFO, logger=real.__name__):
        store = real.ArcadeDBStore.from_env(database=name)
    assert DatabaseDao.exists(client, name) and f"created database '{name}'" in caplog.text
    store.drop()
