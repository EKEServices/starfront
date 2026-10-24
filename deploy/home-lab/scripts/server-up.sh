#!/usr/bin/env bash
# Copy this Mac's Starfront sources to the server host and (re)start the lab
# server there as a systemd user unit. Run by Terraform, or by hand with the
# same LAB_* variables. Uncommitted changes are copied too: that is the point.
#   LAB_SSH=user@omarchy LAB_SRC=~/Develop/EKEServices/Astro/starfront \
#   LAB_PUBLIC_HOST=omarchy.lan LAB_PORT=8800 bash server-up.sh
set -euo pipefail
. "$(dirname "$0")/common.sh"
: "${LAB_SSH:?}" "${LAB_SRC:?}" "${LAB_PUBLIC_HOST:?}" "${LAB_PORT:=8800}"

say "server: copying server/, astrocontrol/, tools/ and requirements.txt to $LAB_SSH:starfront-lab/src/"
remote "$LAB_SSH" 'mkdir -p starfront-lab/src'
rsync -az --delete --exclude __pycache__ --exclude '*.pyc' --exclude .DS_Store -e "$(rsh)" \
  "$LAB_SRC/server" "$LAB_SRC/astrocontrol" "$LAB_SRC/tools" "$LAB_SRC/requirements.txt" \
  "$LAB_SSH:starfront-lab/src/"
remote "$LAB_SSH" bash -s -- "$LAB_PUBLIC_HOST" "$LAB_PORT" < "$here/remote/server.sh"
