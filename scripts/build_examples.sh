#!/usr/bin/env bash
# Rebuild the scripted demo store and the static viewer site in docs/.
#
#   scripts/build_examples.sh
#
# examples/ holds three committed Relate stores judges can inspect:
#   tinyshop/                    the scripted demo (rebuilt here)
#   live-flask/                  Claude Code on Flask 2.3 -> 3.1, unprompted (recorded, not rebuilt)
#   live-claude-code/tinyshop/   Claude Code on tinyshop, prompted (recorded, not rebuilt)
set -euo pipefail
cd "$(dirname "$0")/.."
RECEIPTS="uv run -q receipts"

rm -rf examples/tinyshop
$RECEIPTS demo --dir examples/tinyshop --fast > /dev/null
rm -rf examples/tinyshop/.git
printf '.receipts/*.lock\n.receipts/hook.log\n.receipts/sessions/\n__pycache__/\n' > examples/tinyshop/.gitignore

mkdir -p docs
cp src/receipts/viewer/index.html docs/index.html
cp examples/tinyshop/.receipts/site/before-repair.cypher docs/
cp examples/tinyshop/.receipts/site/after-repair.cypher docs/
rm -rf examples/tinyshop/.receipts/site

datasets='[{"file": "before-repair.cypher", "label": "Demo: after a refactor, before repair"}'
if [[ -f examples/live-flask/.receipts/memory.rl ]]; then
  $RECEIPTS --repo examples/live-flask export --format cypher -o docs/live-flask.cypher
  datasets="$datasets, {\"file\": \"live-flask.cypher\", \"label\": \"Live: Claude Code on Flask, unprompted\"}"
fi
datasets="$datasets, {\"file\": \"after-repair.cypher\", \"label\": \"Demo: after the agent repaired its memory\"}"
if [[ -f examples/live-claude-code/tinyshop/.receipts/memory.rl ]]; then
  $RECEIPTS --repo examples/live-claude-code/tinyshop export --format cypher -o docs/live-session.cypher
  datasets="$datasets, {\"file\": \"live-session.cypher\", \"label\": \"Live: Claude Code on tinyshop\"}"
fi
echo "$datasets]" > docs/datasets.json
echo "examples/ and docs/ rebuilt"
ls -la examples/*/.receipts/memory.rl examples/*/*/.receipts/memory.rl 2>/dev/null || true
