# cursedpp

A Python compiler from a readable template DSL (`.cursed` files) to C preprocessor
macros built on Boost.Preprocessor. Loops/conditionals in generated macros execute
at C compile time via Boost.PP primitives.

## Commands

- `mise install && uv sync` — set up toolchain and env
- `uv run pytest` — run all tests (integration tests need `boost/preprocessor.hpp`; they auto-skip without it)
- `uv run pytest tests/test_emitter.py -k golden` — golden-file emitter tests
- `uv run cursedpp input.cursed -o output.h` — compile a template

## Architecture

Pipeline: source → line-level pass (`parser.py`: pragmas, macro headers, body
vs directive lines, `end` matching) → lark mini-grammars (`grammar.lark`) for
signatures/directives/`{{expr}}` → dataclass AST (`nodes.py`) → semantic checks
(`semantics.py`) → helper IR + macro bodies (`emitter.py`) → helper dedup/
factoring (`collapse.py`) → C header text.

Design spec: `docs/design.md`. Language rules and call-site caveats live there —
consult it before changing DSL syntax or codegen.

## Conventions

- TDD: every feature lands with a failing test first; golden files in `tests/golden/`
  are updated deliberately, never regenerated blindly.
- Golden templates carry their own e2e specs as `#? INVOCATION => expected`
  comments (or `#?` + `#=>` lines for multiple assertions); `test_integration.py`
  discovers them and verifies each through `cc -E`. New goldens must include
  specs — `test_every_golden_template_has_specs` enforces it.
- Generated helpers are namespaced `CURSEDPP_<MACRO>_*` (shared collapsed helpers:
  `CURSEDPP_H<n>` in first-use order — output must stay deterministic).
- The `BOOST_PP_` prefix is never hardcoded in emitter output paths; always go
  through the configured prefix (`--pp-prefix` / `@pragma pp_prefix`).
