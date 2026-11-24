#!/usr/bin/env bash
# Deploy the static viewer to Vercel production by hand.
#
# Normally you don't need this: the Vercel project is connected to the GitHub
# repo, so every push to main deploys automatically. vercel.json tells Vercel to
# serve docs/ as-is (no install, no build); Relate runs in the visitor's browser
# as WebAssembly.
#
#   vercel login                  # once
#   scripts/deploy_vercel.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${VERCEL_PROJECT:-receipts-memory}"
if [[ ! -f .vercel/project.json ]]; then
  vercel link --yes --project "$PROJECT"
fi
vercel deploy --prod --yes
