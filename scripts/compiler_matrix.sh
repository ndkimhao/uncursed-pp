#!/usr/bin/env bash
# Spec-check every template with every supported compiler, nproc-parallel.
# Workers emit single-line start/finish events for live progress; the full
# per-compiler logs print afterward as (GitHub-collapsible) groups, so
# parallel output never interleaves. A failing compiler doesn't stop the
# others but still fails the run.
#
# Compilers default to the supported matrix; override with arguments:
#   scripts/compiler_matrix.sh gcc-14 clang-19
set -euo pipefail

# ── worker mode: spec-check ONE compiler (the xargs fan-out below
# re-invokes this script with --worker <cc>) ─────────────────────────
if [ "${1:-}" = "--worker" ]; then
  cc=$2
  echo "[$cc] started  ($($cc --version | head -n 1))"
  if make speccheck CHECK_CC="$cc" > "$LOGDIR/$cc.log" 2>&1; then
    echo "[$cc] passed   ($(tail -n 1 "$LOGDIR/$cc.log"))"
    exit 0
  fi
  echo "[$cc] FAILED   (full log in the group below)"
  exit 1
fi

ccs=("$@")
[ ${#ccs[@]} -gt 0 ] || ccs=(gcc-12 gcc-13 gcc-14 gcc-15 gcc-16
                             clang-19 clang-20 clang-21 clang-22)

LOGDIR=$(mktemp -d)
export LOGDIR
trap 'rm -rf "$LOGDIR"' EXIT
SELF=$(readlink -f "$0")

jobs=$(nproc)
echo "spec-checking ${#ccs[@]} compilers, $jobs-way parallel ($jobs cores)"

failed=0
printf '%s\n' "${ccs[@]}" | xargs -P "$jobs" -n1 -- "$SELF" --worker || failed=1

for cc in "${ccs[@]}"; do
  echo "::group::${cc} ($($cc --version | head -n 1 || true))"
  cat "$LOGDIR/$cc.log" 2>/dev/null || echo "(no log produced)"
  echo "::endgroup::"
done
exit "$failed"
