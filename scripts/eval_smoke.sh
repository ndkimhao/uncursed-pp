#!/usr/bin/env bash
# End-to-end smoke of `uncursed-pp-eval`: invoke mode and --update-specs
# mode, each with and without --format. Needs cc and (for the format
# legs) clang-format.
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
[ "$out" = "{ 1, 2 }" ] || { echo "unexpected expansion: $out"; exit 1; }

echo "── invoke, plain, through vendored boost"
$RUN uncursed-pp-eval tests/golden/basics/pair.uncursed 'PAIR((a, b))' -- -I .boost-pp/include \
    | grep -q 'S{ a | b }'

echo "── invoke, --format"
$RUN uncursed-pp-eval "$TMP/plain.uncursed" 'PAIR(1, 2)' --format | grep -q '1, 2'

echo "── update-specs, plain"
$RUN uncursed-pp-eval "$TMP/plain.uncursed" --update-specs | grep -q 'filled 1'
grep -q '#=>     { seen, filled }' "$TMP/plain.uncursed"
grep -vq '<???>' "$TMP/plain.uncursed"
$RUN uncursed-pp-check "$TMP/plain.uncursed" > /dev/null

echo "── update-specs, --format (multi-line fill)"
cat > "$TMP/fmt.uncursed" <<'EOF'
@macro FN($n)
void {{$n}}(void) { alpha(); beta(); }
@endmacro

#?  FN(go)
#=> <???>
EOF
$RUN uncursed-pp-eval "$TMP/fmt.uncursed" --update-specs --format | grep -q 'filled 1'
[ "$(grep -c '#=>' "$TMP/fmt.uncursed")" -ge 2 ] || { echo "expected a multi-line fill"; exit 1; }
$RUN uncursed-pp-check "$TMP/fmt.uncursed" > /dev/null

echo "eval smoke: all legs passed"
