#!/usr/bin/env bash
# The hash of what server-up.sh copies to the server host (server/, astrocontrol/,
# tools/ and requirements.txt, caches left out), as Terraform external-data JSON:
# a change to any of it on this Mac, committed or not, redeploys the server.
set -euo pipefail
cd "$1"
hash=$(find server astrocontrol tools requirements.txt -type f \
         -not -path '*/__pycache__/*' -not -name '*.pyc' -not -name '.DS_Store' -print0 \
       | LC_ALL=C sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-16)
printf '{"hash":"%s"}\n' "$hash"
