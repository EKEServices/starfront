#!/usr/bin/env bash
# Put collab-hub and collab-worker for each OS into deploy/home-lab/bin/<os>/,
# where Terraform looks for them. Rust is not cross-compiled, so each OS's
# binaries come from somewhere that builds natively:
#
#   fetch-binaries.sh --mac                     cargo build --release here (also collab-uploader,
#                                               which the smoke test and real uploads use)
#   fetch-binaries.sh --from-run RUN_ID         the archives of a Rapid Stacker Collab worker-release
#                                               run (gh run download): linux-x64 and windows-x64
#   fetch-binaries.sh --build-on user@host      build on the Linux host itself, over SSH (it needs
#                                               rustup, cmake and read access to the private
#                                               rapid-stacker repository)
#
# The worker-release workflow is manual (Actions > worker-release > Run) and
# signs, uploads and tags nothing; its archives are unsigned, which the lab is
# fine with (nothing installs them through the desktop's signed-manifest path).
set -euo pipefail
. "$(dirname "$0")/common.sh"
bin="$(cd "$here/.." && pwd)/bin"
rsc="${RSC:-$(cd "$here/../../../.." && pwd)/Rust/rapid-stacker-collab}"
[ -f "$rsc/worker/Cargo.toml" ] || die "no Rapid Stacker Collab checkout at $rsc (set RSC=...)"
[ $# -gt 0 ] || { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --mac)
      say "building the macOS binaries in $rsc/worker ..."
      (cd "$rsc/worker" && CARGO_NET_GIT_FETCH_WITH_CLI=true cargo build --release \
         -p collab-hub -p collab-worker -p collab-uploader)
      mkdir -p "$bin/macos-arm64"
      for b in collab-hub collab-worker collab-uploader; do cp -f "$rsc/worker/target/release/$b" "$bin/macos-arm64/"; done
      shift ;;
    --from-run)
      [ $# -ge 2 ] || die "--from-run needs a run id"
      tmp="$(mktemp -d)"
      say "downloading the archives of run $2 ..."
      gh run download "$2" -R EKE-Services/rapid-stacker-collab -D "$tmp"
      for archive in $(find "$tmp" -name 'collab-*-*-*.zip' -o -name 'collab-*-*-*.tar.gz'); do
        case "$archive" in
          *-linux-x64.tar.gz) os=linux-x64 ;; *-windows-x64.zip) os=windows-x64 ;;
          *-macos-arm64.zip) os=macos-arm64 ;; *) continue ;;
        esac
        out="$tmp/x-$os-$(basename "$archive")"; mkdir -p "$out" "$bin/$os"
        case "$archive" in *.zip) unzip -q -o "$archive" -d "$out" ;; *) tar -xzf "$archive" -C "$out" ;; esac
        find "$out" -type f \( -name collab-hub -o -name collab-worker -o -name 'collab-hub.exe' -o -name 'collab-worker.exe' \) \
          -exec cp -f {} "$bin/$os/" \;
      done
      rm -rf "$tmp"
      shift 2 ;;
    --build-on)
      [ $# -ge 2 ] || die "--build-on needs user@host"
      say "building on $2 (the source is copied from $rsc) ..."
      remote "$2" 'mkdir -p starfront-lab/build'
      rsync -az --delete --exclude target --exclude node_modules --exclude .git -e "$(rsh)" "$rsc/" "$2:starfront-lab/build/rsc/"
      remote "$2" 'cd starfront-lab/build/rsc/worker && CARGO_NET_GIT_FETCH_WITH_CLI=true cargo build --release -p collab-hub -p collab-worker'
      mkdir -p "$bin/linux-x64"
      copy "$2:starfront-lab/build/rsc/worker/target/release/collab-hub" "$2:starfront-lab/build/rsc/worker/target/release/collab-worker" "$bin/linux-x64/"
      shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
say "binaries now in $bin:"
ls -l "$bin"/*/ 2>/dev/null
