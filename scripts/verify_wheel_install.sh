#!/usr/bin/env bash
# PREP-1.6 — clean-venv install gate.
#
# Proves the consumer path end to end: build the wheel, install it (WITH its deps,
# resolved from the index — no direct-URL dep must appear) into a throwaway venv that
# does NOT see the source tree, then assert the public surface and the shipped data
# all resolve from site-packages. This is what `uv add rag-wright` must do for a new
# product (TexWright). Run from anywhere: `bash scripts/verify_wheel_install.sh`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[1/4] build wheel"
rm -rf dist
uv build --wheel
WHL="$(ls "$ROOT"/dist/*.whl | head -1)"
echo "       $WHL"

echo "[2/4] create clean venv ($TMP/venv)"
uv venv "$TMP/venv" --python 3.12

echo "[3/4] install the wheel + deps into the clean venv (no source tree)"
uv pip install --python "$TMP/venv/bin/python" "$WHL"

echo "[4/4] assert the public surface + shipped data, from site-packages"
# cd out of the repo so `import rag_wright` can only resolve to the installed package.
cd "$TMP"
"$TMP/venv/bin/python" - <<'PY'
import pathlib
import rag_wright
from rag_wright import api

need = [
    "EngineConfig", "StoreConfig", "WorkspaceHandle", "open_workspace",
    "ainvoke_subgraph", "invoke_model", "ainvoke_model",
    "register_capability", "load_reference_pack", "reference_pack",
    "kg_read", "kg_write", "kg_edges", "entities_by_name", "measure_usage",
    "parse_document", "source_document",
]
missing = [n for n in need if not hasattr(api, n)]
assert not missing, f"missing from rag_wright.api: {missing}"

pkg = pathlib.Path(rag_wright.__file__).parent
assert "site-packages" in str(pkg), f"imported from {pkg}, not site-packages (source-tree leak)"
assert (pkg / "py.typed").exists(), "py.typed marker not installed"
ttls = sorted(p.name for p in (pkg / "ontology").glob("*.ttl"))
assert ttls, "reference-pack .ttl not installed"
skills = sorted(p.parent.name for p in (pkg / "skills").glob("*/SKILL.md"))
assert skills, "capability SKILL.md not installed"

print("OK  import root :", pkg)
print("OK  api symbols :", len(need), "present")
print("OK  py.typed    : shipped")
print("OK  ontology ttl:", ttls)
print("OK  skills       :", len(skills), "SKILL.md packaged")
PY
echo "PREP-1.6 PASS — the wheel installs clean and the public surface + data resolve from site-packages."
