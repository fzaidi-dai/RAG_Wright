"""PS-5 (G20): fetch (or publish) the reference pack's trained classifier weights.

The reference pack loads two model sets: the clause-type SetFit ensemble (`setfit_clause/cap128b_*`) and the
29-dimension property fleet (`laya/*`, `setfit/*`, as listed in `dim_fleet.json`). They are published as one
uncompressed tar per model directory plus a `manifest.json` (each archive's sha256 and size) under a versioned prefix,
and fetched into the models root (`RAG_MODELS_DIR`, see `rag_wright.models.weights`). The weights were trained on
restrictively licensed data, so the bucket is private: fetching needs Google Cloud credentials with read access
(`GOOGLE_APPLICATION_CREDENTIALS`).

    uv run python scripts/fetch_reference_models.py                    # fetch into the models root
    uv run python scripts/fetch_reference_models.py --dest data/models # fetch into a given directory
    uv run python scripts/fetch_reference_models.py --only setfit_clause/cap128b_bge  # fetch only some models
    uv run python scripts/fetch_reference_models.py --publish          # publish from the models root (maintainers)

A fetch verifies every archive's sha256 before extracting it and skips a model already fetched at the same checksum.
`--remote` may also be a local directory (used by the tests).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

REMOTE = "gs://dreamai-pocs-ragwright-ingest/models/reference-pack/v1"
_CLAUSE_MODELS = ("cap128b_legalbert", "cap128b_bge", "cap128b_mpnet")  # production_setfit_clause_classifier's set
_FLEET = Path(__file__).resolve().parents[1] / "src" / "rag_wright" / "packs" / "contracts" / "spans" / "dim_fleet.json"
_MARKER = ".fetched-sha256"


class ChecksumError(RuntimeError):
    """A downloaded archive does not match the manifest's sha256."""


def reference_model_dirs() -> list[str]:
    """Every model directory the reference pack loads, relative to the models root."""
    fleet = json.loads(_FLEET.read_text())
    dirs = [f"setfit_clause/{m}" for m in _CLAUSE_MODELS]
    for spec in fleet.values():
        d = f"{'laya' if spec['framework'] == 'laya' else 'setfit'}/{spec['model']}"
        if d not in dirs:
            dirs.append(d)
    return dirs


class _Remote:
    """A GCS prefix (`gs://bucket/prefix`) or a local directory, with the four operations publish/fetch need."""

    def __init__(self, location: str) -> None:
        self._gcs = location.startswith("gs://")
        if self._gcs:
            from google.cloud import storage

            bucket, _, prefix = location[len("gs://"):].partition("/")
            self._bucket, self._prefix = storage.Client().bucket(bucket), prefix.strip("/")
        else:
            self._dir = Path(location)

    def _blob(self, name: str):
        return self._bucket.blob(f"{self._prefix}/{name}", chunk_size=64 * 1024 * 1024)

    def put_file(self, path: Path, name: str) -> None:
        if self._gcs:
            self._blob(name).upload_from_filename(str(path), timeout=600)
        else:
            (self._dir / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, self._dir / name)

    def put_text(self, text: str, name: str) -> None:
        if self._gcs:
            self._blob(name).upload_from_string(text, content_type="application/json")
        else:
            (self._dir / name).parent.mkdir(parents=True, exist_ok=True)
            (self._dir / name).write_text(text)

    def get_text(self, name: str) -> str:
        return self._blob(name).download_as_bytes().decode() if self._gcs else (self._dir / name).read_text()

    def get_file(self, name: str, path: Path) -> None:
        if self._gcs:
            self._blob(name).download_to_filename(str(path), timeout=600)
        else:
            shutil.copyfile(self._dir / name, path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB"


def publish(root: Path, remote: str, *, dirs: list[str] | None = None, log: Callable[[str], None] = print) -> None:
    """Archive each model directory under `root` (one tar each), upload it, then write the manifest last (so a
    partial publish never looks complete)."""
    dirs = dirs or reference_model_dirs()
    missing = [d for d in dirs if not (Path(root) / d).is_dir()]
    if missing:
        raise FileNotFoundError(f"not under {root}: {missing}")
    target = _Remote(remote)
    entries, total = [], 0
    log(f"[publish] start N={len(dirs)} from {root} to {remote}")
    with tempfile.TemporaryDirectory() as tmp:
        for i, d in enumerate(dirs, 1):
            archive = Path(tmp) / "model.tar"
            with tarfile.open(archive, "w") as tar:
                tar.add(Path(root) / d, arcname=d)
            entry = {"dir": d, "archive": f"{d}.tar", "sha256": _sha256(archive), "bytes": archive.stat().st_size}
            target.put_file(archive, entry["archive"])
            archive.unlink()
            entries.append(entry)
            total += entry["bytes"]
            log(f"[publish] {i}/{len(dirs)} {d} {_gb(entry['bytes'])} (total {_gb(total)})")
    target.put_text(json.dumps({"version": 1, "archives": entries}, indent=2), "manifest.json")
    log(f"[publish] done {len(entries)} archives, {_gb(total)}")


def fetch(remote: str, dest: Path, *, only: list[str] | None = None, log: Callable[[str], None] = print) -> None:
    """Download every archive in the manifest (or only the model directories in `only`) into `dest`, verify its
    sha256, then extract it. A model already fetched at the same checksum is skipped; an archive that fails its
    checksum raises `ChecksumError` and is never extracted; a name in `only` that is not published raises
    `KeyError`."""
    source = _Remote(remote)
    manifest = json.loads(source.get_text("manifest.json"))
    archives = manifest["archives"]
    if only is not None:
        unknown = set(only) - {e["dir"] for e in archives}
        if unknown:
            raise KeyError(f"not in the manifest: {sorted(unknown)}")
        archives = [e for e in archives if e["dir"] in only]
    dest = Path(dest)
    staging = dest / ".download"
    staging.mkdir(parents=True, exist_ok=True)
    log(f"[fetch] start N={len(archives)} ({_gb(sum(e['bytes'] for e in archives))}) from {remote} to {dest}")
    for i, entry in enumerate(archives, 1):
        model = dest / entry["dir"]
        marker = model / _MARKER
        if marker.is_file() and marker.read_text().strip() == entry["sha256"]:
            log(f"[fetch] {i}/{len(archives)} {entry['dir']} skip (already fetched)")
            continue
        archive = staging / "model.tar"
        source.get_file(entry["archive"], archive)
        if _sha256(archive) != entry["sha256"]:
            archive.unlink()
            raise ChecksumError(f"{entry['archive']}: sha256 does not match the manifest")
        if model.exists():
            shutil.rmtree(model)
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter="data")  # rejects absolute paths and `..`
        archive.unlink()
        marker.write_text(entry["sha256"])
        log(f"[fetch] {i}/{len(archives)} {entry['dir']} downloaded and verified ({_gb(entry['bytes'])})")
    staging.rmdir()
    log(f"[fetch] done {len(archives)} models in {dest}")


def main(argv: list[str] | None = None) -> int:
    from rag_wright.models.weights import models_dir

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--remote", default=REMOTE, help=f"gs:// prefix or local directory (default {REMOTE})")
    ap.add_argument("--dest", type=Path, default=None, help="where to fetch to (default: the models root)")
    ap.add_argument("--only", nargs="+", default=None, help="fetch only these model directories (e.g. laya/<model>)")
    ap.add_argument("--publish", action="store_true", help="publish from the models root instead of fetching")
    args = ap.parse_args(argv)
    log = lambda line: print(line, flush=True)  # noqa: E731
    if args.publish:
        publish(models_dir(), args.remote, log=log)
    else:
        fetch(args.remote, args.dest or models_dir(), only=args.only, log=log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
