"""Shared pytest support: keep live/opt-in tests out of the default hermetic suite.

Tests marked `model` (live OpenRouter call) or `store` (local ArcadeDB) are skipped unless their
marker is explicitly selected with `-m`, so `uv run pytest` stays offline and fast. When such a run
is requested, `.env` is loaded first (minimal parser, no new dependency) so the live test has its
credentials without the developer exporting anything.
"""

from __future__ import annotations

import os
import pathlib

import pytest

_OPT_IN = ("model", "store", "parse", "embed", "rerank", "ner")


def _load_dotenv() -> None:
    env = pathlib.Path(__file__).parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    markexpr = config.option.markexpr
    selected = {opt for opt in _OPT_IN if opt in markexpr}
    if selected:
        _load_dotenv()
    for item in items:
        for opt in _OPT_IN:
            # check the actual marker, not `item.keywords` (which also matches path parts like
            # `tests/store/`, wrongly skipping hermetic tests that merely live under that directory).
            if item.get_closest_marker(opt) is not None and opt not in selected:
                item.add_marker(pytest.mark.skip(reason=f"opt-in: run with -m {opt}"))
