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

# The 7 capability libraries (ADR-0001) + `modal` (training-infra grounding, added 2026-07-25 for the
# distilled cross-encoder path: ground the Modal SDK API we write training scripts against, per the
# library-grounding rule) + `docling_graph` (added 2026-07-28 for the unified-contract-KG path: the
# schema-driven KG extractor GP-1B adopted and KG-0..KG-6 lean on heavily — `template from-ontology`,
# the extraction runners, the model seam; ground its API first-class alongside the `kg-extraction-recipe`
# Skill + live CLI, so nothing is guessed). modal_proto (low-level gRPC stubs) is excluded as noise.
PKGS="docling docling_core docling_graph FlagEmbedding langchain_openai mcp spacy arcadedb_python modal langgraph deepagents rdflib pyshacl langchain_mcp_adapters langfuse olefile pypdfium2"
SP="$(uv run python -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
STAGE="$HOME/.graphify/rag-wright-framework/src"
OUT="graphify-out/framework/graph.json"

echo "[framework] site-packages: $SP"
rm -rf "$STAGE"; mkdir -p "$STAGE"

N_PKGS=$(echo $PKGS | wc -w | tr -d ' '); i=0
echo "[framework] staging N=$N_PKGS packages"
for pkg in $PKGS; do
  i=$((i + 1)); echo "[framework] stage $i/$N_PKGS $pkg"
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

# vllm (ADR-0039: the product substrate's inference engine) is NOT pip-installed on this Mac (CUDA/Linux),
# so it is staged from a shallow source clone at ~/.graphify-src/vllm. SCOPED to the surfaces our code + the
# self-hosted stack actually drive -- the OpenAI serving layer, sampling params, structured/guided-output +
# reasoning config, request/response protocol -- NOT the huge model-executor/attention/kernel internals.
# Refresh the clone with: git -C ~/.graphify-src/vllm pull  (or re-clone).
VLLM_SRC="$HOME/.graphify-src/vllm/vllm"
if [ -d "$VLLM_SRC" ]; then
  echo "[framework] staging vllm (scoped serving/sampling/structured-output surfaces) from clone..."
  mkdir -p "$STAGE/vllm"
  for sub in __init__.py version.py sampling_params.py pooling_params.py outputs.py inputs \
             entrypoints config reasoning; do
    [ -e "$VLLM_SRC/$sub" ] && rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyi' \
      --exclude='tests' --exclude='test' "$VLLM_SRC/$sub" "$STAGE/vllm/"
  done
else
  echo "[framework] NOTE: vllm clone not at $VLLM_SRC -> skipped (git clone --depth 1 https://github.com/vllm-project/vllm.git ~/.graphify-src/vllm)"
fi

# fastmcp (MCP-PROTO: the MCP-tool server framework, ADR pending) -- staged from the CLONE, not site-packages,
# because FastMCP versions change rapidly (the whole reason for repo-level grounding) and the 3.x monorepo keeps
# the core package under fastmcp_slim/fastmcp. `graphify clone https://github.com/jlowin/fastmcp` puts it at
# ~/.graphify/repos/jlowin/fastmcp; refresh with `git -C ~/.graphify/repos/jlowin/fastmcp pull`. Keep the clone
# checked out at the tag matching the installed version for grounding accuracy.
FASTMCP_SRC="$HOME/.graphify/repos/jlowin/fastmcp/fastmcp_slim/fastmcp"
if [ -d "$FASTMCP_SRC" ]; then
  echo "[framework] staging fastmcp (core server/tools/client) from clone..."
  rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyi' --exclude='tests' --exclude='test' \
    "$FASTMCP_SRC" "$STAGE/"
else
  echo "[framework] NOTE: fastmcp clone not at $FASTMCP_SRC -> skipped (graphify clone https://github.com/jlowin/fastmcp)"
fi

echo "[framework] extracting (AST only, no LLM)..."
graphify update "$STAGE" >/dev/null 2>&1 || { echo "[framework] graphify update failed"; exit 1; }

BUILT="$STAGE/graphify-out/graph.json"
[ -f "$BUILT" ] || { echo "[framework] no graph produced at $BUILT"; exit 1; }

mkdir -p "$(dirname "$OUT")"
if [ -f "$OUT" ]; then
  cp "$OUT" "$OUT.bak-$(date +%Y%m%d-%H%M%S)"
fi
cp "$BUILT" "$OUT"

# Merge the ArcadeDB SQL-vector docs (the surface arcadedb-python does not wrap: vector.fuse,
# vector.sparseNeighbors, the LSM_*_VECTOR DDL) into the same single framework index. The extraction
# was LLM-produced once (scripts/extract_arcadedb_docs.py, via OpenRouter/DeepSeek V4 Pro) into a
# committed artifact; this merge is deterministic and free. Additive (dedup=False): docs added, the
# Python-AST grounding untouched. Re-run the extraction only when docs/vendor/arcadedb/ changes.
GRAPHIFY_PY="$(cat graphify-out/.graphify_python 2>/dev/null || command -v graphify)"
if [ -f docs/vendor/arcadedb/arcadedb-docs-extraction.json ] && [ -n "$GRAPHIFY_PY" ]; then
  echo "[framework] merging ArcadeDB SQL-vector docs (additive, no LLM)..."
  "$GRAPHIFY_PY" scripts/merge_docs_into_framework.py "$OUT" || echo "[framework] WARNING: doc merge failed"
fi

nodes="$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['nodes']))" "$OUT")"
echo "[framework] rebuilt -> $OUT ($nodes nodes)"
echo "[framework] backups: $(ls graphify-out/framework/graph.json.bak-* 2>/dev/null | wc -l | tr -d ' ') kept in graphify-out/framework/"
