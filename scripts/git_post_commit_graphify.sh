#!/usr/bin/env bash
# Refresh the Graphify PROJECT index after a commit so grounding stays current
# (CLAUDE.md session-start step 4; the project graph maps this repo's own code/docs).
#
# Installed as .git/hooks/post-commit by scripts/install_git_hooks.sh. Kept in the repo so the
# logic is version-controlled; the hook itself is a thin caller and is per-clone (git hooks are
# not tracked), so re-run the installer after cloning.
#
# - Project index: ALWAYS. `graphify update .` is content-cached and fast, and graphify-out/ is
#   gitignored, so it is cheap and produces no git noise. This is the index that actually goes
#   stale as each task adds code.
# - Framework index: only when the commit changed dependencies (uv.lock / pyproject.toml).
#   refresh_framework_graph.sh does a full ~11s rebuild over the 7 grounding-authority libraries
#   (ADR-0001), so gating it on a real dependency change avoids paying that on every commit.
#
# Best-effort: a refresh failure never disrupts anything (the commit is already made).
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 0

command -v graphify >/dev/null 2>&1 && graphify update . >/dev/null 2>&1 || true

if git diff-tree --no-commit-id --name-only -r HEAD | grep -qE '^(uv\.lock|pyproject\.toml)$'; then
  echo "[graphify] dependencies changed -> refreshing framework graph"
  bash scripts/refresh_framework_graph.sh >/dev/null 2>&1 || true
fi
