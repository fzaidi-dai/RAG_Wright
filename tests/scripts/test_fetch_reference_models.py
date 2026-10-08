"""PS-5 (G20): the reference pack's trained weights are published as checksummed per-model archives and fetched
into the models root. Hermetic: a local directory stands in for the GCS prefix."""
from __future__ import annotations

import json

import pytest

from scripts.fetch_reference_models import ChecksumError, fetch, publish, reference_model_dirs


def _models(root, dirs):
    for i, d in enumerate(dirs):
        (root / d).mkdir(parents=True)
        (root / d / "model.safetensors").write_bytes(bytes([i]) * 1000)
        (root / d / "config.json").write_text(json.dumps({"i": i}))
    return root


def test_the_archive_covers_every_model_the_reference_pack_loads():
    from pathlib import Path

    fleet = json.loads(Path("src/rag_wright/packs/contracts/spans/dim_fleet.json").read_text())
    dirs = set(reference_model_dirs())
    assert {f"{'laya' if s['framework'] == 'laya' else 'setfit'}/{s['model']}" for s in fleet.values()} <= dirs
    assert {f"setfit_clause/cap128b_{b}" for b in ("legalbert", "bge", "mpnet")} <= dirs


def test_publish_then_fetch_round_trips_with_checksums(tmp_path):
    dirs = ["laya/A_group", "setfit/dim_x", "setfit_clause/cap128b_bge"]
    src = _models(tmp_path / "src", dirs)
    remote = tmp_path / "remote"
    lines = []
    publish(src, str(remote), dirs=dirs, log=lines.append)
    manifest = json.loads((remote / "manifest.json").read_text())
    assert [e["dir"] for e in manifest["archives"]] == dirs and all(len(e["sha256"]) == 64 for e in manifest["archives"])
    assert lines[0].startswith("[publish] start N=3") and any("3/3" in line for line in lines)

    dest = tmp_path / "dest"
    fetch(str(remote), dest, log=lines.append)
    for d in dirs:
        assert (dest / d / "model.safetensors").read_bytes() == (src / d / "model.safetensors").read_bytes()
    assert not (dest / ".download").exists() or not any((dest / ".download").iterdir())

    fetched = []
    fetch(str(remote), dest, log=fetched.append)  # already present and verified -> nothing downloaded
    assert any("skip" in line for line in fetched) and not any("download" in line for line in fetched[1:-1])


def test_a_corrupted_archive_is_rejected_and_not_extracted(tmp_path):
    dirs = ["setfit/dim_x"]
    remote = tmp_path / "remote"
    publish(_models(tmp_path / "src", dirs), str(remote), dirs=dirs, log=lambda _l: None)
    archive = next(p for p in remote.rglob("*.tar"))
    archive.write_bytes(archive.read_bytes()[:-10] + b"0123456789")
    with pytest.raises(ChecksumError):
        fetch(str(remote), tmp_path / "dest", log=lambda _l: None)
    assert not (tmp_path / "dest" / "setfit" / "dim_x").exists()


def test_fetch_can_be_limited_to_some_models(tmp_path):
    dirs = ["laya/A_group", "setfit/dim_x", "setfit_clause/cap128b_bge"]
    remote = tmp_path / "remote"
    publish(_models(tmp_path / "src", dirs), str(remote), dirs=dirs, log=lambda _l: None)
    fetch(str(remote), tmp_path / "dest", only=["setfit/dim_x"], log=lambda _l: None)
    assert (tmp_path / "dest" / "setfit" / "dim_x" / "model.safetensors").exists()
    assert not (tmp_path / "dest" / "laya").exists()
    with pytest.raises(KeyError):
        fetch(str(remote), tmp_path / "dest", only=["setfit/not_published"], log=lambda _l: None)
