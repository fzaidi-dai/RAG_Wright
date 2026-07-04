#!/usr/bin/env bash
# Rebuild the Graphify FRAMEWORK graph from the installed grounding-authority libraries.
#
# Run after a dependency version change (uv add / uv sync). No LLM is used; this is pure AST
# extraction (~10-15s). The framework graph is the grounding authority for every library call the
# capabilities make (CLAUDE.md library-grounding rule); it must reflect the installed versions.
#
# Scope (ADR-0001): exactly the 7 libraries the capabilities drive, noise-pruned. Pruning is done
# by controlling what is staged for extraction (graphify has no extraction-time exclude flag): the
# packages are rsynced into a staging tree with test trees, bytecode, and the spaCy language tables
# excluded, then extracted. Output lands at graphify-out/framework/graph.json (what graph_status.sh
# and the SessionStart hook read). The previous graph is backed up first.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PKGS="docling docling_core FlagEmbedding langchain_openai mcp spacy arcadedb_python"
SP="$(uv run python -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
STAGE="$HOME/.graphify/rag-wright-framework/src"
OUT="graphify-out/framework/graph.json"

echo "[framework] site-packages: $SP"
rm -rf "$STAGE"; mkdir -p "$STAGE"

for pkg in $PKGS; do
  if [ ! -d "$SP/$pkg" ]; then
    echo "[framework] WARNING: $pkg not found in site-packages -> skipped"
    continue
  fi
  extra=()
  [ "$pkg" = "spacy" ] && extra=(--exclude='lang')  # drop spaCy language tables (ADR-0001)
  rsync -a \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' --exclude='*.pyi' \
    --exclude='*.dist-info' --exclude='tests' --exclude='test' \
    ${extra[@]+"${extra[@]}"} "$SP/$pkg" "$STAGE/"
done

echo "[framework] extracting (AST only, no LLM)..."
graphify update "$STAGE" >/dev/null 2>&1 || { echo "[framework] graphify update failed"; exit 1; }

BUILT="$STAGE/graphify-out/graph.json"
[ -f "$BUILT" ] || { echo "[framework] no graph produced at $BUILT"; exit 1; }

mkdir -p "$(dirname "$OUT")"
if [ -f "$OUT" ]; then
  cp "$OUT" "$OUT.bak-$(date +%Y%m%d-%H%M%S)"
fi
cp "$BUILT" "$OUT"

nodes="$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['nodes']))" "$OUT")"
echo "[framework] rebuilt -> $OUT ($nodes nodes)"
echo "[framework] backups: $(ls graphify-out/framework/graph.json.bak-* 2>/dev/null | wc -l | tr -d ' ') kept in graphify-out/framework/"
