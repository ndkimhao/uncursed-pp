# uncursed-pp

A Python compiler from a readable template DSL (`.uncursed` files) to C preprocessor
macros built on Boost.Preprocessor. Loops/conditionals in generated macros execute
at C compile time via Boost.PP primitives.

## Commands

- `make setup` — toolchain (mise), env (uv), vendored Boost.PP (git clone into `.boost-pp/`)
- `make check` — mypy --strict + full pytest (e2e specs run against the vendored boost)
- `uv run pytest tests/test_golden.py` — exact golden-header comparison
- `uv run uncursed-pp input.uncursed -o output.h` — compile a template

## Architecture

Pipeline: source → Jinja2 meta pass (`meta.py`: alt delimiters `<<% %>>`/`<<{ }>>`/`<<# #>>`,
skipped when marker-free) → line-level pass (`parser.py`: pragmas, macro headers, body
vs directive lines, `@endmacro` matching) → lark mini-grammars (`grammar.lark`) for
signatures/directives/`{{expr}}` → dataclass AST (`nodes.py`) → semantic
checks + helper IR + macro bodies (`emitter.py`) → helper dedup/factoring
(`collapse.py`) → C header text.

Design spec: `docs/design.md`. Language rules and call-site caveats live there —
consult it before changing DSL syntax or codegen. Codegen performance work must
follow `docs/optimization.md` (measurement methodology, applied optimizations,
and refuted ideas — the refuted list is binding).

## Conventions

- TDD: every feature lands with a failing test first; golden files in `tests/golden/`
  are updated deliberately, never regenerated blindly.
- Anything codegen-related — a new feature, a bug fix, a behavior change — must
  land with a golden template (committed `.h` + `#?`/`#=>` specs) pinning it, not
  only Python tests. For bug fixes that means a regression golden reproducing
  the trap (see `tests/golden/compose/collapse_traps.uncursed`).
- Tests are organized one file per feature (`tests/test_loops.py`,
  `test_conditionals.py`, `test_expressions.py`, `test_defaults.py`,
  `test_named_args.py`, `test_variadic.py`, `test_config.py`, ...); they hold
  AST and error-path checks only. Codegen shape belongs in golden headers,
  runtime behavior in specs — avoid string-matching generated code in Python.
- Goldens live in category dirs (`tests/golden/{basics,loops,conditionals,
  expressions,args,variadic,compose}/`), discovered recursively. Each
  template carries e2e specs as comments — one `#? INVOCATION` line followed
  by `#=> expected` line(s). The `#=>` lines are the COMPLETE expected
  expansion: `test_e2e_specs.py` runs each through `cc -E` and requires
  whole-output equality (canonicalized), so a missing or extra emitted token
  fails; the exact marker `<...>` in a `#=>` line is an explicit wildcard
  for any token run. New goldens must include specs —
  `test_every_golden_template_has_specs` enforces it.
  `examples/` is a second golden root with identical rules (committed .h,
  mandatory #? specs, deliberate regeneration) — its templates are
  human-facing documentation, so keep the prose accurate too.
- Generated helpers are namespaced `UNCURSED_PP_<MACRO>_*` (shared collapsed helpers:
  `UNCURSED_PP_<FILESTEM>_H<n>` in first-use order — deterministic, and
  namespaced per file so independently generated headers cannot collide).
- The `BOOST_PP_` prefix is never hardcoded in emitter output paths; always go
  through the configured prefix (`@pragma pp_prefix`; EmitConfig for API use).
