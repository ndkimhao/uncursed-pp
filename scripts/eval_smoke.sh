#!/usr/bin/env bash
# End-to-end smoke of `uncursed-pp-eval`: invoke mode and --update-specs
# mode, each with and without --format. Needs cc and (for the format
# legs) clang-format. Every leg logs the tool's actual output.
set -euo pipefail

RUN="mise exec -- uv run"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/plain.uncursed" <<'EOF'
@macro PAIR($a, $b)
{ {{$a}}, {{$b}} }
@endmacro

#?  PAIR(seen, filled)
#=> <???>
EOF

echo "── invoke, plain"
out=$($RUN uncursed-pp-eval "$TMP/plain.uncursed" 'PAIR(1, 2)')
echo "$out"
[ "$out" = "{ 1, 2 }" ] || { echo "FAIL: unexpected expansion"; exit 1; }

echo "── invoke, plain, through vendored boost"
out=$($RUN uncursed-pp-eval tests/golden/basics/pair.uncursed 'PAIR((a, b))' -- -I .boost-pp/include)
echo "$out"
echo "$out" | grep -q 'S{ a | b }' || { echo "FAIL: boost expansion"; exit 1; }

echo "── invoke, --format"
out=$($RUN uncursed-pp-eval "$TMP/plain.uncursed" 'PAIR(1, 2)' --format)
echo "$out"
echo "$out" | grep -q '1, 2' || { echo "FAIL: formatted expansion"; exit 1; }

echo "── update-specs, plain"
$RUN uncursed-pp-eval "$TMP/plain.uncursed" --update-specs
echo "filled template now reads:"
grep -A1 '#?' "$TMP/plain.uncursed"
grep -q '#=>     { seen, filled }' "$TMP/plain.uncursed" || { echo "FAIL: fill content"; exit 1; }
if grep -q '<???>' "$TMP/plain.uncursed"; then echo "FAIL: sentinel left behind"; exit 1; fi
$RUN uncursed-pp-check "$TMP/plain.uncursed"

echo "── update-specs, --format (multi-line fill)"
cat > "$TMP/fmt.uncursed" <<'EOF'
@macro FN($n)
void {{$n}}(void) { alpha(); beta(); }
@endmacro

#?  FN(go)
#=> <???>
EOF
$RUN uncursed-pp-eval "$TMP/fmt.uncursed" --update-specs --format
echo "filled template now reads:"
grep -A5 '#?' "$TMP/fmt.uncursed"
[ "$(grep -c '#=>' "$TMP/fmt.uncursed")" -ge 2 ] || { echo "FAIL: expected a multi-line fill"; exit 1; }
$RUN uncursed-pp-check "$TMP/fmt.uncursed"

echo "eval smoke: all legs passed"
