#!/usr/bin/env bash
# Install the repo's git hooks into .git/hooks (git hooks are per-clone, not tracked).
# Run once per clone: `bash scripts/install_git_hooks.sh`.
set -euo pipefail
cd "$(dirname "$0")/.."

HOOK_DIR="$(git rev-parse --git-path hooks)"
cat > "$HOOK_DIR/post-commit" <<'HOOK'
#!/usr/bin/env bash
exec bash "$(git rev-parse --show-toplevel)/scripts/git_post_commit_graphify.sh"
HOOK
chmod +x "$HOOK_DIR/post-commit"
echo "Installed post-commit hook -> scripts/git_post_commit_graphify.sh"
