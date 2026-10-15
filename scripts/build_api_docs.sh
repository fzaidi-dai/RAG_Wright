#!/usr/bin/env bash
# PREP-3.1: regenerate the generated API reference (docs/api/) from the live public tiers, and (PS-43) the
# generated tables in docs/harness.md.
# Drift guard: tests/arch/test_api_docs_current.py fails when the committed reference differs from a fresh render.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python scripts/build_api_docs.py
uv run python scripts/build_harness_doc.py
