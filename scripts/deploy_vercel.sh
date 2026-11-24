#!/usr/bin/env bash
# Deploy the static viewer (docs/) to Vercel production.
#
#   vercel login                  # once, in your own terminal
#   scripts/deploy_vercel.sh      # every time docs/ changes
#
# The site is plain static files (index.html, datasets.json, *.cypher); there is
# no build step. Relate runs in the visitor's browser as WebAssembly.
set -euo pipefail
cd "$(dirname "$0")/../docs"
PROJECT="${VERCEL_PROJECT:-receipts-memory}"
if [[ ! -f .vercel/project.json ]]; then
  vercel link --yes --project "$PROJECT"
fi
vercel deploy --prod --yes
