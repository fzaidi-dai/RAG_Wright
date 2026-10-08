"""CLS-A (FR-I.4): per-dimension property CLASSIFIERS behind one seam, for Step-3a "Extract Clauses".

A `DimClassifier` maps a span's text to top-k (value, probability) for ONE closed-vocab PropertyDimension, replacing
the per-span LLM call for that dimension. Two runtimes implement the seam:
  - `SetFitDimClassifier` — a SentenceTransformer body + joblib sklearn head on disk (NO `setfit` dep at serve time),
    the 14 SetFit keepers (13 LegalBERT + 1 all-mpnet).
  - `LayaDimClassifier` — a fine-tuned Laya (ModernBERT-large RL decision) checkpoint via the `laya` package, the 1
    keeper (termination_right) SetFit couldn't crack.

STANDING serving philosophy (same as the model-profile seam for the LLM): device is NOT pinned — use a GPU if one is
available (CUDA, else Apple MPS), else CPU; the caller may override. Load ONCE (these are heavy, esp. Laya ~820MB).
Nothing upstream changes: the hybrid extractor (CLS-B) composes these with the LLM behind the `PropertyExtractor`
Protocol.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from rag_wright.packs.contracts.schemas.property import PropertyDimension


def auto_device(pref: Optional[str] = None) -> str:
    """GPU-if-available-else-CPU: honor an explicit choice, else CUDA -> MPS -> CPU. Never pin a device in code."""
    if pref:
        return pref
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001 - torch import/probe failure -> CPU is the safe floor
        pass
    return "cpu"


@runtime_checkable
class DimClassifier(Protocol):
    """Span text -> ranked (value, probability) for one dimension. `classify` returns top-k, highest first."""

    dim: PropertyDimension

    def classify(self, span_text: str) -> list[tuple[str, float]]: ...


class SetFitDimClassifier:
    """A SetFit keeper served as body+head (no `setfit` dep): `body.encode` -> `head.predict_proba` -> top-k. Honors
    the body's configured normalization; `head.classes_` columns map to the dimension's values."""

    def __init__(self, dim: PropertyDimension, model_dir, *, top_k: int = 1, device: Optional[str] = None,
                 batch_size: int = 32) -> None:
        import json
        from pathlib import Path

        import joblib
        from sentence_transformers import SentenceTransformer

        self.dim = dim
        self._top_k = top_k
        self._batch = batch_size
        d = Path(model_dir)
        self._device = auto_device(device)
        self._body = SentenceTransformer(str(d), device=self._device)
        self._head = joblib.load(d / "model_head.pkl")
        self._labels = [str(c) for c in self._head.classes_]
        # SetFit stores whether the body normalizes embeddings; mirror it so serve-time matches train-time.
        cfg = d / "config_setfit.json"
        self._normalize = bool(json.loads(cfg.read_text()).get("normalize_embeddings", True)) if cfg.exists() else True

    def classify(self, span_text: str) -> list[tuple[str, float]]:
        import numpy as np
        emb = self._body.encode([span_text], normalize_embeddings=self._normalize, batch_size=self._batch)
        proba = np.asarray(self._head.predict_proba(emb), dtype=float)[0]
        order = np.argsort(-proba)[: self._top_k]
        return [(self._labels[i], float(proba[i])) for i in order]


class LayaDimClassifier:
    """A fine-tuned Laya checkpoint served via the `laya` package: one `choice` question over the dimension's values,
    a single non-autoregressive forward pass. Device auto (CUDA/MPS/CPU) inside laya.load; loaded once."""

    def __init__(self, dim: PropertyDimension, model_dir, *, instructions: str, criteria: dict[str, str],
                 top_k: int = 1, device: Optional[str] = None, agent: Any = None) -> None:
        self.dim = dim
        self._top_k = top_k
        self._q = {dim.value: {"type": "choice", "instructions": instructions, "criteria": criteria}}
        # A GROUP checkpoint serves several dims from ONE model: pass a shared pre-loaded `agent` so it is loaded
        # ONCE (a ModernBERT-large is ~820MB) and every dim in the group queries the same forward-pass backbone.
        if agent is not None:
            self._agent = agent
        else:
            import laya  # laya.load device=None -> CUDA else MPS else CPU; pass an explicit override if given.
            self._agent = laya.load(str(model_dir), device=device)

    def classify(self, span_text: str) -> list[tuple[str, float]]:
        ans = self._agent.predict(span_text, self._q)["answers"][self.dim.value]
        probs = ans.get("probabilities", {})
        ranked = sorted(probs.items(), key=lambda kv: -kv[1])[: self._top_k]
        return [(k, float(v)) for k, v in ranked]


class DimClassifierRegistry:
    """Loads the configured per-dimension classifiers ONCE and serves them by dimension. Missing dims (uncovered)
    return None; the hybrid extractor (CLS-B/C) extracts ONLY its 7 numeric/open `RESIDUAL_LLM_DIMS` via the LLM,
    never an uncovered classifier dim (the corpus-starved dims are left unextracted until CLS-F sources data)."""

    def __init__(self, classifiers: dict[PropertyDimension, DimClassifier]) -> None:
        self._by_dim = dict(classifiers)

    def get(self, dim: PropertyDimension) -> Optional[DimClassifier]:
        return self._by_dim.get(dim)

    def covers(self, dim: PropertyDimension) -> bool:
        return dim in self._by_dim

    @property
    def dims(self) -> list[PropertyDimension]:
        return list(self._by_dim)


# CLS-D: the production 21-dim best-of-both fleet. `dim_fleet.json` (committed config) maps each dim to its
# framework + local model dir + serving params; model weights live under the models root (`RAG_MODELS_DIR`, PS-5).
# A LAYA group checkpoint serves several dims -> load each unique model ONCE and share the agent.
_FLEET_CONFIG = Path(__file__).with_name("dim_fleet.json")


def load_dim_registry(config_path=None, *, models_dir=None, device: Optional[str] = None) -> "DimClassifierRegistry":
    """Load the committed fleet: shared Laya group agents (loaded once) + per-dim SetFit models, behind the
    device-agnostic seam. Raises FileNotFoundError with the missing path if a checkpoint has not been fetched."""
    import json

    from rag_wright.models.weights import models_dir as models_root

    cfg = json.loads(Path(config_path or _FLEET_CONFIG).read_text())
    base = Path(models_dir or models_root())
    laya_agents: dict[str, Any] = {}  # model dir name -> loaded laya agent (shared across its dims)
    classifiers: dict[PropertyDimension, DimClassifier] = {}
    for dim_str, spec in cfg.items():
        dim = PropertyDimension(dim_str)
        top_k = int(spec.get("top_k", 1))
        if spec["framework"] == "laya":
            mdir = base / "laya" / spec["model"]
            if not mdir.exists():
                raise FileNotFoundError(f"laya checkpoint not fetched: {mdir}")
            agent = laya_agents.get(spec["model"])
            if agent is None:
                import laya
                agent = laya.load(str(mdir), device=device)  # device=None -> CUDA/MPS/CPU
                laya_agents[spec["model"]] = agent
            q = spec["question"]
            classifiers[dim] = LayaDimClassifier(dim, mdir, instructions=q["instructions"],
                                                 criteria=q["criteria"], top_k=top_k, agent=agent)
        else:
            mdir = base / "setfit" / spec["model"]
            if not mdir.exists():
                raise FileNotFoundError(f"setfit checkpoint not fetched: {mdir}")
            classifiers[dim] = SetFitDimClassifier(dim, mdir, top_k=top_k, device=device)
    return DimClassifierRegistry(classifiers)
