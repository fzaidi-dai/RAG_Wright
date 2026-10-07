#!/usr/bin/env bash
# PREP-3.1: regenerate the generated API reference (docs/api/README.md) from the live rag_wright.api symbols.
# Drift guard: tests/arch/test_api_docs_current.py fails when the committed reference differs from a fresh render.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python scripts/build_api_docs.py
