"""EP-CORE-3 (ADR-0118): the engine ships an EMPTY ARD catalog -- a developer registers their product's
capabilities at runtime. The ENGINE's own test suite, however, treats the contract/compliance pack as its worked
domain, so we load the reference pack here, at conftest IMPORT time (before any test module is collected -- some
tests parametrize over `MANIFEST_SPECS` at collection). A downstream product would NOT do this; it registers its own.

Network guard (ING-9): a test that is not marked live (`model`, `store`, `parse`, `embed`, `rerank`, `ner`, `fleet`)
must not reach the network. Unmocked model calls in the default suite cost real money twice (a vision-OCR
escalation on a synthetic PDF), so any non-local lookup is blocked AND fails the test, naming the host.
"""
import os
import re
import socket

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # litellm's bundled price table, not a GitHub fetch
# Cached Hugging Face models (docling layout, tokenizers) load without a hub freshness check. The hub reads this at
# import, so it is set here; a live run that must download a model first sets HF_HUB_OFFLINE=0.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from rag_wright.capabilities.manifests import load_reference_pack  # noqa: E402 - after the litellm env default

load_reference_pack()

_LIVE_MARKERS = {"model", "store", "parse", "embed", "rerank", "ner", "fleet"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


@pytest.fixture(autouse=True)
def _no_network_unless_live(request, monkeypatch):
    if _LIVE_MARKERS & {m.name for m in request.node.iter_markers()}:
        yield
        return
    attempts: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name in _LOCAL_HOSTS or name.startswith("127."):
            return real_getaddrinfo(host, *args, **kwargs)
        attempts.append(name)
        raise OSError(f"test network guard: lookup of {name} blocked (mock it, or mark the test live)")

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
    yield
    if attempts:
        pytest.fail(f"this test tried to reach {sorted(set(attempts))}: mock the call, or mark the test live")


# ING-CLEAN: a live store test leaves no database behind. The databases created DURING a `store` test whose names
# follow the test convention are dropped after it; anything that existed before the test is never touched.
_TEST_DB = re.compile(r"^(ragwright_test_\w+|ragwright_doccheck_\w+|\w+_live)$")


def _database_names() -> set[str] | None:
    import base64
    import json
    import urllib.request

    host, port = os.environ.get("ARCADEDB_HOST", "localhost"), os.environ.get("ARCADEDB_PORT", "2480")
    user, password = os.environ.get("ARCADEDB_USER"), os.environ.get("ARCADEDB_PASSWORD")
    if not (user and password):
        return None
    req = urllib.request.Request(f"http://{host}:{port}/api/v1/databases")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - the local test ArcadeDB
            return set(json.load(resp)["result"])
    except Exception:  # noqa: BLE001 - no store reachable: nothing to clean
        return None


@pytest.fixture(autouse=True)
def _drop_test_databases(request):
    if not request.node.get_closest_marker("store"):
        yield
        return
    from dotenv import load_dotenv

    load_dotenv(".env")
    before = _database_names()
    yield
    after = _database_names()
    if before is None or after is None:
        return
    from rag_wright.store.arcadedb import ArcadeDBStore

    for name in sorted(after - before):
        if _TEST_DB.match(name):
            store = ArcadeDBStore.from_env(database=name)
            store.drop()
            store.close()
