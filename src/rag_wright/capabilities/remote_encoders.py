"""MS1-6 (MODAL-STACK-1, ADR-0039): HTTP-client adapters that route the query-side encoders to the co-located
A100 GPU-services stack (`scripts/modal_stack_a100.py`).

The LLM surfaces route to vLLM-Granite via the model seam (`RAG_SERVING`, MS1-1/3); BGE-M3 embed + LegalBERT
classify are DIRECT classes, not through the seam, so they get their own remote adapters here. Each matches
the local interface it replaces (`embedding.Embedder` / `LegalBertFunctionClassifier`), so it drops into the
same query seam -- the product then runs the query's non-LLM GPU work (embed + classify) on the SAME A100 as
vLLM, the whole point of D2. Selected by the `STACK_URL` env (mirroring the LLM's `RAG_SERVING` switch); unset
-> the local in-process encoders. `post` is injected so the adapters are hermetically testable (no network).
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable


def post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class RemoteBGEEmbedder:
    """BGE-M3 dense/sparse encode via the A100 `/embed` endpoint. Matches `embedding.BGEM3Embedder`
    (encode_dense / encode_sparse / encode_batch), producing vectors in the SAME space as the KG's stored
    span vectors (verified byte-identical in MS1-5b)."""

    def __init__(self, base_url: str, *, post: Callable[..., dict] = post_json) -> None:
        self._url = base_url.rstrip("/") + "/embed"
        self._post = post

    def encode_dense(self, text: str) -> list[float]:
        return self._post(self._url, {"text": text})["dense"][0]

    def encode_sparse(self, text: str) -> dict[int, float]:
        return {int(k): float(v) for k, v in self._post(self._url, {"text": text})["sparse"][0].items()}

    def encode_batch(self, texts: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]:
        out = self._post(self._url, {"texts": list(texts)})
        sparse = [{int(k): float(v) for k, v in s.items()} for s in out["sparse"]]
        return out["dense"], sparse


def stack_url() -> str | None:
    """The co-located A100 stack base URL from `STACK_URL` (the query-encoder serving switch), or None (local)."""
    return os.getenv("STACK_URL")


def query_embedder(*, post: Callable[..., dict] = post_json) -> Any:
    """The query-side embedder: the remote A100 `/embed` adapter when `STACK_URL` is set, else the local
    in-process `BGEM3Embedder` (dev/default). One env (`STACK_URL`) moves the query's embed onto the A100."""
    url = stack_url()
    if url:
        return RemoteBGEEmbedder(url, post=post)
    from rag_wright.capabilities.embedding import BGEM3Embedder

    return BGEM3Embedder()
