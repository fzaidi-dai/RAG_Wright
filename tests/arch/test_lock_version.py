"""PS-25: the release PR bumps `uv.lock` with `pyproject.toml`, so the lock never lags a release.

release-please bumps `pyproject.toml`'s version; `release-please-config.json` lists `uv.lock` as an extra TOML file
whose `rag-wright` package version it bumps too. If that ever stops matching (the JSONPath relies on release-please's
TOML parser), release-please only logs a warning: this test is what fails, on the release PR."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _lock_version() -> str:
    lock = tomllib.loads((_ROOT / "uv.lock").read_text(encoding="utf-8"))
    (version,) = [p["version"] for p in lock["package"] if p["name"] == "rag-wright"]
    return version


def test_the_lock_carries_the_project_version():
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert _lock_version() == project, "uv.lock lags pyproject.toml: run `uv lock` (and see PS-25)"


def test_the_release_config_bumps_the_lock():
    config = json.loads((_ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    extra = config["packages"]["."].get("extra-files", [])
    assert {"type": "toml", "path": "uv.lock",
            "jsonpath": "$.package[?(@.name.value==='rag-wright')].version"} in extra
