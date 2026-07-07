#!/usr/bin/env bash
# SessionStart hook: surface Graphify graph status so the agent knows the index is
# available (playbook section 5). Grounding resolves against the framework graph; the
# project graph maps the repo. Kept fast and non-fatal — never blocks a session.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
FRAMEWORK="$ROOT/graphify-out/framework/graph.json"
PROJECT="$ROOT/graphify-out/graph.json"

# Prefer the interpreter Graphify was installed under; fall back to python3.
PY="python3"
[ -f "$ROOT/graphify-out/.graphify_python" ] && PY="$(cat "$ROOT/graphify-out/.graphify_python")"

count() { # $1 = graph.json path -> node count or "-"
  [ -f "$1" ] || { echo "-"; return; }
  "$PY" -c "import json,sys; print(len(json.load(open(sys.argv[1]))['nodes']))" "$1" 2>/dev/null || echo "?"
}

fw=$(count "$FRAMEWORK")
pj=$(count "$PROJECT")

echo "Graphify index status:"
if [ "$fw" = "-" ]; then
  echo "  framework graph: MISSING — rebuild before grounding any library call"
else
  echo "  framework graph: $fw nodes (grounding authority: docling, docling-core, FlagEmbedding, langchain_openai, mcp, spacy, arcadedb_python [Python AST] + ArcadeDB SQL vector docs [LLM-extracted])"
fi
if [ "$pj" = "-" ]; then
  echo "  project graph:   MISSING — run /graphify . to build"
else
  echo "  project graph:   $pj nodes (repo + SPEC.md/CLAUDE.md/playbook)"
fi
echo "  query: graphify query \"<q>\" --graph graphify-out/framework/graph.json   (project graph: default path)"
exit 0
