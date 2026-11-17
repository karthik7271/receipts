#!/usr/bin/env bash
# Rebuild examples/ (committed Relate stores judges can inspect) and docs/
# (the static in-browser viewer for GitHub Pages).
#
#   scripts/build_examples.sh [path/to/live/tinyshop]
#
# The optional argument is a tinyshop checkout that a real Claude Code session
# worked in (with its .receipts/memory.rl); it becomes the "live session" example.
set -euo pipefail
cd "$(dirname "$0")/.."
RECEIPTS="uv run -q receipts"

rm -rf examples/tinyshop
$RECEIPTS demo --dir examples/tinyshop --fast > /dev/null
rm -rf examples/tinyshop/.git
printf '.receipts/*.lock\n.receipts/hook.log\n__pycache__/\n' > examples/tinyshop/.gitignore

mkdir -p docs
cp src/receipts/viewer/index.html docs/index.html
cp examples/tinyshop/.receipts/site/before-repair.cypher docs/
cp examples/tinyshop/.receipts/site/after-repair.cypher docs/
rm -rf examples/tinyshop/.receipts/site
datasets='[{"file": "before-repair.cypher", "label": "Demo: after the refactor (before repair)"},
 {"file": "after-repair.cypher", "label": "Demo: after the agent repaired its memory"}'

LIVE="${1:-}"
if [[ -n "$LIVE" && -f "$LIVE/.receipts/memory.rl" ]]; then
  rm -rf examples/live-claude-code/tinyshop
  mkdir -p examples/live-claude-code/tinyshop
  rsync -a --exclude .git --exclude '*.lock' --exclude hook.log --exclude __pycache__ \
    --exclude .mcp.json --exclude .claude "$LIVE/" examples/live-claude-code/tinyshop/
  printf '.receipts/*.lock\n.receipts/hook.log\n__pycache__/\n' > examples/live-claude-code/tinyshop/.gitignore
  $RECEIPTS --repo examples/live-claude-code/tinyshop export --format cypher -o docs/live-session.cypher
  datasets="$datasets,
 {\"file\": \"live-session.cypher\", \"label\": \"Live: memories written by Claude Code via MCP\"}"
fi
echo "$datasets]" > docs/datasets.json
echo "examples/ and docs/ rebuilt"
ls -la examples/*/.receipts/memory.rl examples/*/*/.receipts/memory.rl 2>/dev/null || true
