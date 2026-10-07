"""EP-CORE-3 (ADR-0118): the engine ships an EMPTY ARD catalog -- a developer registers their product's
capabilities at runtime. The ENGINE's own test suite, however, treats the contract/compliance pack as its worked
domain, so we load the reference pack here, at conftest IMPORT time (before any test module is collected -- some
tests parametrize over `MANIFEST_SPECS` at collection). A downstream product would NOT do this; it registers its own.

Network guard (ING-9): a test that is not marked live (`model`, `store`, `parse`, `embed`, `rerank`, `ner`, `fleet`)
must not reach the network. Unmocked model calls in the default suite cost real money twice (a vision-OCR
escalation on a synthetic PDF), so any non-local lookup is blocked AND fails the test, naming the host.
"""
import os
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
