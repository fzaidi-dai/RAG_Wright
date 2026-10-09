"""PS-5 (G20): where trained classifier weights live -- ONE models root for every classifier a pack loads.

`models_dir()` resolves, in order: `RAG_MODELS_DIR`; else the engine checkout's `data/models` when it exists (an
editable install, today's layout); else `./data/models` under the working directory (a product's own copy of the
weights, the layout an installed engine uses). A pack's loaders take paths under it (`<root>/<model set>/...`).
The reference pack's weights are fetched into it with `scripts/fetch_reference_models.py`.
"""
from __future__ import annotations

import os
from pathlib import Path

_CHECKOUT = Path(__file__).resolve().parents[3]  # src/rag_wright/models/weights.py -> the repository root


def models_dir() -> Path:
    """The models root (see the module docstring for the resolution order)."""
    env = os.environ.get("RAG_MODELS_DIR")
    if env:
        return Path(env)
    checkout = _CHECKOUT / "data" / "models"
    if checkout.is_dir():
        return checkout
    return Path("data") / "models"
