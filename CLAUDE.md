# cursedpp

A Python compiler from a readable template DSL (`.cursed` files) to C preprocessor
macros built on Boost.Preprocessor. Loops/conditionals in generated macros execute
at C compile time via Boost.PP primitives.

## Commands

- `make setup` — toolchain (mise), env (uv), vendored Boost.PP (git clone into `.boost-pp/`)
- `make check` — mypy --strict + full pytest (e2e specs run against the vendored boost)
- `uv run pytest tests/test_golden.py` — exact golden-header comparison
- `uv run cursedpp input.cursed -o output.h` — compile a template

## Architecture

Pipeline: source → line-level pass (`parser.py`: pragmas, macro headers, body
vs directive lines, `end` matching) → lark mini-grammars (`grammar.lark`) for
signatures/directives/`{{expr}}` → dataclass AST (`nodes.py`) → semantic checks
(`semantics.py`) → helper IR + macro bodies (`emitter.py`) → helper dedup/
factoring (`collapse.py`) → C header text.

Design spec: `docs/design.md`. Language rules and call-site caveats live there —
consult it before changing DSL syntax or codegen. Codegen performance work must
follow `docs/optimization.md` (measurement methodology, applied optimizations,
and refuted ideas — the refuted list is binding).

## Conventions

- TDD: every feature lands with a failing test first; golden files in `tests/golden/`
  are updated deliberately, never regenerated blindly.
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
  fails. New goldens must include specs —
  `test_every_golden_template_has_specs` enforces it.
- Generated helpers are namespaced `CURSEDPP_<MACRO>_*` (shared collapsed helpers:
  `CURSEDPP_H<n>` in first-use order — output must stay deterministic).
- The `BOOST_PP_` prefix is never hardcoded in emitter output paths; always go
  through the configured prefix (`@pragma pp_prefix`; EmitConfig for API use).
