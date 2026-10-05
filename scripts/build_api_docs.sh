#!/usr/bin/env bash
# PREP-3.1: regenerate the generated API reference (docs/api/README.md) from the live rag_wright.api symbols.
# Drift guard: run this, then `git diff --exit-code docs/api` — a non-empty diff means the committed reference is
# stale. (Add that two-liner to CI to fail on drift.)
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python scripts/build_api_docs.py
