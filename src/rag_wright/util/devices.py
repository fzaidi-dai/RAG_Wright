"""PS-55 (TexWright's 2026-10-25 request): where the engine runs its torch models, and why only one family is on Metal.

PyTorch's Apple Metal (`mps`) backend is NOT safe for concurrent use: one process shares one Metal command stream,
and two threads running models on it at once abort the process inside Metal (`failed assertion _status <
MTLCommandBufferStatusCommitted`) or segfault it -- `abort()`, never an exception. RuleWright measured it (their
T-7.25: 4 crashes in ~8 live ingests on Metal, 0 in 6 on CPU) and TexWright hit it in a web process. The engine
makes it reachable without any concurrency in a product's code: an ingest embeds one document while it parses
another, and a server embeds a query while an upload parses.

So the engine's automatic choice keeps ONE model family on Metal, the BGE family (the BGE-M3 embedder and the BGE
reranker, the hot path, ~2x faster there), and every call to it on Metal holds `METAL_LOCK`, one lock per process,
so two instances (a query embedder and a workspace's, two workspaces, the embedder and the reranker) never run on
Metal at once. Docling's layout, table and OCR models and the reference pack's classifiers run on CPU where Metal
would otherwise be chosen (Docling's output is identical; measured on the engine's fixtures).

Explicit choices always win (`EMBED_DEVICE`, `DOCLING_DEVICE`, `RAG_SETFIT_DEVICE`, a `device=` argument), `mps`
included -- which keeps the crash reproducible. A CUDA or CPU-only host is unchanged: this only decides between
Metal and CPU.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Optional

METAL_LOCK = threading.RLock()  # every engine model call that runs on Metal holds it (one per process)
_SHARED_MODELS: dict[tuple, tuple[Any, Any]] = {}  # PS-65: (kind, model, precision, device) -> (model, its lock)
_SHARED_GUARD = threading.Lock()


def metal_available() -> bool:
    try:
        import torch

        return bool(torch.backends.mps.is_available())
    except Exception:  # noqa: BLE001 - torch absent or probing failed: no Metal
        return False


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def torch_device(explicit: Optional[str] = None, *, env: Optional[str] = None, metal: bool = False) -> str:
    """The device for one engine model: `explicit`, else the `env` variable, else CUDA when present, else Metal
    only for the family allowed on it (`metal=True`, the BGE family), else CPU."""
    chosen = explicit or (os.environ.get(env) if env else None)
    if chosen:
        return chosen
    if _cuda_available():
        return "cuda"
    if metal and metal_available():
        return "mps"
    return "cpu"


def docling_device() -> str:
    """Docling's accelerator device: `DOCLING_DEVICE` when set, else `cpu` where Docling's `auto` would pick Metal,
    else `auto` (Docling's own choice, CUDA included)."""
    chosen = os.environ.get("DOCLING_DEVICE")
    if chosen:
        return chosen
    return "cpu" if metal_available() and not _cuda_available() else "auto"


def model_lock(device: str) -> "threading.Lock | threading.RLock":
    """The lock a model instance holds around each call: the process-wide `METAL_LOCK` on Metal, else its own."""
    return METAL_LOCK if str(device).startswith("mps") else threading.Lock()


def shared_model(key: tuple, device: str, build: Callable[[], Any]) -> tuple[Any, Any]:
    """PS-65 (TexWright's 2026-11-09 request): the process's one loaded model for `key` (kind, model name, precision,
    device), built by `build` on first use, and the ONE lock every wrapper of it holds around each call (`model_lock`:
    `METAL_LOCK` on Metal). Sharing the lock with the model is what keeps issue 0016's serialization: FlagEmbedding
    mutates a model in place on every encode, so two wrappers of one model must never run it at once. Concurrent
    first use builds once. A loaded model stays for the life of the process (dropping a workspace does not free it)."""
    with _SHARED_GUARD:
        hit = _SHARED_MODELS.get(key)
        if hit is None:
            hit = _SHARED_MODELS[key] = (build(), model_lock(device))
        return hit
