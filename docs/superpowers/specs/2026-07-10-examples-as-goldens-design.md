# examples/ as a golden-tested tree — design

Date: 2026-07-10
Status: approved (design discussion in session; spec pending user review)

## Purpose

`tests/golden/` is a per-feature regression suite, but it is written for the
compiler, not for people: terse macros, minimal comments, buried under
`tests/`. The `examples/` tree is written for a human learning the DSL:

- **verbose prose** explaining each feature — what it does, why you'd want
  it, and the call-site rules that surprise newcomers;
- **realistic, freshly-authored macros** (never copied from goldens, so
  nothing can drift);
- the **user-facing transformation** front and center: what a macro *call*
  expands to as final C source. The `#? INVOCATION` / `#=> expansion` spec
  format already encodes exactly that, so the teaching artifact and the test
  artifact are the same lines.

Every example is simultaneously a golden test: its generated `.h` is
committed and byte-compared, and its specs run token-exact through real
`cc -E` against vendored Boost.PP.

## Structure

```
examples/
  README.md                  reading order; how to compile one yourself
  features/                  one DSL/codegen feature each, numbered
    01-for.uncursed          + 01-for.h
    ...                        (17 templates, see inventory)
  combined/                  several features composing into one artifact
    bitflags.uncursed        + bitflags.h
    state-machine.uncursed   + state-machine.h
    test-registry.uncursed   + test-registry.h
  uncursed_pp_runtime.h      committed companion so the tree compiles standalone
tests/golden/                unchanged — terse regression fixtures
```

`examples/example.uncursed` (the 8-macro feature tour) is **deleted**: its
macros are byte-identical copies of eight existing goldens and each new
feature example supersedes its slot.

## Harness integration (approach A: second golden root)

`tests/conftest.py`:

- `golden_templates()` returns templates from BOTH roots:
  `tests/golden/**/*.uncursed` and `examples/**/*.uncursed`, sorted.
- `golden_id()` becomes root-aware: `basics/pair` (goldens) vs
  `examples/features/01-for` — unique, readable pytest IDs.

Everything downstream applies to examples with no further change:

- `test_golden.py` — committed `.h` byte-comparison, and the
  every-template-has-a-header meta-test;
- `test_e2e_specs.py` — `#?`/`#=>` specs through `cc -E`, plus the
  every-template-has-specs and every-macro-is-exercised meta-tests.

Retired alongside:

- `test_example_file_compiles_and_all_macros_expand` in `test_e2e_specs.py`
  (its hardcoded invocation/expectation lists are subsumed by inline specs);
- `Makefile`'s `example:` target repoints at
  `examples/combined/bitflags.uncursed`.

Runtime-header freshness: `test_config.py` already pins
`tests/golden/uncursed_pp_runtime.h` against `runtime_header(EmitConfig())`;
extend that test to pin `examples/uncursed_pp_runtime.h` identically. The
config example's custom-named companion (see 15 below) is ALSO committed and
pinned with its own `EmitConfig(helper_prefix=...)` — generated-file
freshness must never depend on a human remembering to regenerate.

## Inventory — examples/features/ (one feature each)

Numbered as a reading order. Each: prose header (motivation + rules), one
realistic macro (or minimal set), specs showing call → final C.

| # | File | Feature | Scenario |
|---|------|---------|----------|
| 01 | `01-for` | `@for` over `seq<token>` | errno-style error-code enum |
| 02 | `02-for-tuple` | `@for (a, b) in xs` destructuring | CPU register declarations |
| 03 | `03-nested-loops` | loops in loops (4-deep limit) | jump table over mode × width |
| 04 | `04-join` | `@join … with`, `as` binding | function prototype arg list |
| 05 | `05-if-else` | `@if`/`@else` + `len()` | scalar vs vector constructor |
| 06 | `06-is-paren` | `is_paren()` | normalize bare/parenthesized attrs |
| 07 | `07-concat` | `concat()`; pasting is never implicit | getter/setter name generation |
| 08 | `08-stringize` | `stringize()` on computed tokens | error-code → string table |
| 09 | `09-remove-parens` | `remove_parens()` comma protection | template-type param with commas |
| 10 | `10-let` | `@let` generation-time bindings | reusing a computed token |
| 11 | `11-tail-defaults` | `level = INFO` arity dispatch | TRACE logger |
| 12 | `12-named-args` | `named WIDTH = 640`, any order/subset | window constructor |
| 13 | `13-named-variadic` | `named FLAGS =` accepting bare commas | flag sets |
| 14 | `14-variadic` | `variadic`, `variadic<tuple<…>>` | mixed-arity call sites |
| 15 | `15-pragma-config` | `@pragma helper_prefix` + `runtime_name` | vendoring generated macros |
| 16 | `16-helper-collapse` | codegen: shared collapsed helpers | two macros, one `_H1` in the `.h` |
| 17 | `17-adjacency` | codegen: `pre{{x}}` never pastes | why `concat()` exists |

Notes:

- **15**: only `helper_prefix` and `runtime_name` are exercised with specs
  (verified feasible e2e). `pp_prefix`, `pp_include`, `pp_include_dir`,
  `include`, `loop_chain`, `loop_chain_limit` are covered in prose only — a
  spec'd `@pragma include <stdint.h>` would force the specs to reproduce
  every typedef stdint.h emits (specs are whole-output equality), and a
  custom `pp_prefix` cannot resolve against the vendored boost headers.
  Its custom-named companion runtime header is committed next to it.
- **16 and 17** teach *codegen* behavior. Their prose points at named lines
  in their committed `.h` (e.g. the shared `UNCURSED_PP_<STEM>_H1`); the
  specs prove the behavior at the call site.
- **16 is currently blocked**: consumption-chain codegen (commit `2e6fb13`)
  emits a `CAT(<M>_CH1_, size)` family reference that the collapse pass
  cannot see, so merging two macros' identical chain entries leaves the
  small-seq path expanding to an undefined `UNCURSED_PP_<M>_CH1_<n>` token.
  Repro: two macros with identical no-free-var loop bodies, call with seq
  ≤ chain limit. Being fixed in a parallel session; example 16's specs
  double as the e2e regression test for that fix. Do not land 16 before it.

## Inventory — examples/combined/

Deliberately distinct from the five existing `compose/` goldens (dispatch,
enum_system, lookup_table, pipeline, reflect):

- `bitflags.uncursed` — one flag list → enum with shifted values,
  `to_string`, combined ALL-mask. (`@for`, `concat`, `stringize`, `@join`)
- `state-machine.uncursed` — transition list → state enum + `step()`
  dispatcher. (tuples, nested access, `@if`, composition)
- `test-registry.uncursed` — test list → registration prototypes + a runner
  invoking each. (`variadic<tuple<…>>`, `@join`, tail defaults)

## Authoring conventions

- Prose in `#` comments, written to be read top-to-bottom; every call-site
  caveat that applies to the feature (double parens, comma wrapping,
  non-empty seqs, keyword-not-defined) restated where it bites.
- Specs immediately follow the macro they exercise, with a one-line comment
  saying what each demonstrates; edge cases (empty default, repeated
  keyword, single-element seq) get their own specs.
- Every macro defined must be exercised by a spec or composed into one that
  is (meta-test enforced).
- Filenames map to valid C helper stems automatically
  (`01-for` → `UNCURSED_PP_01_FOR_`; verified).

## Docs

- `examples/README.md`: reading order, one-paragraph orientation, and a
  compile-it-yourself snippet (`uv run uncursed-pp examples/features/01-for.uncursed -o /tmp/out.h`).
- `README.md` and `docs/guide.md` currently link `examples/example.uncursed`
  (deleted): repoint to `examples/` and the reading order.
- `CLAUDE.md`: goldens section gains one line — examples/ is a second golden
  root with identical rules (specs mandatory, committed .h, deliberate
  regeneration).

## Testing / verification

- `make check` green: mypy --strict (conftest changes are typed) + full
  pytest. Net-new: ~20 golden comparisons + ~60–80 spec invocations through
  `cc -E`.
- Examples are goldens: `.h` regenerated only deliberately, diffs read, not
  trusted (CLAUDE.md rule applies unchanged).
- The `#=>` lines are complete expansions — authoring an example means
  running the real preprocessor, not predicting it.

## Out of scope

- Rearranging `tests/golden/` (stays as-is).
- A docs-site or generated HTML — the `.uncursed` files ARE the docs.
- Back-porting verbose prose into existing goldens.
