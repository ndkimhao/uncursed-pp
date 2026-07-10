# uncursed-pp — a template DSL compiled to Boost.Preprocessor C macros

## Context

Writing Boost.PP macro machinery by hand (FOR_EACH helpers, overload dispatchers,
keyword-arg probes) is unreadable and error-prone. uncursed-pp is a Python compiler:
you write macros in a readable, web-template-style DSL (`.uncursed` files); it emits
a C header of `#define`s built on Boost.PP, so the macros consume variable data at
C compile time.

## DSL specification (approved sketch)

```text
# Comments start with '#'. A file holds any number of macro definitions.
# Arg types: token (default), seq<T>, tuple<$name, ...> (named elems),
# tuple / tuple<T...> (unbounded: variable count of T elements), variadic.

# ── 1. Loop over a seq of tuples ────────────────────────────────────
@macro DECLARE_FIELDS($fields: seq<tuple<$type, $name>>)
@for ($type, $name) in $fields
  {{$type}} {{$name}};
@end
@endmacro
#   DECLARE_FIELDS(((int, x))((float, y)))  →  int x; float y;

# ── 2. Inline @join — separator between items ───────────────────────
@macro PROTO($name, $args: seq<tuple<$type, $argname>>)
void {{$name}}(@join $args with ", ": {{$type}} {{$argname}}@end);
@endmacro
#   PROTO(draw, ((struct ctx *, ctx))((int, flags)))
#     →  void draw(struct ctx * ctx, int flags);

# ── 3. Element access: named for tuples, indexed for seqs ───────────
@macro GETTER($field: tuple<$type, $name>)
@let $getter := concat(get_, $field.$name)
{{$field.$type}} {{$getter}}(const struct self *s) {
  return s->{{$field.$name}};
}
@endmacro
# concat() is EXPLICIT token pasting (BOOST_PP_CAT); adjacent text never
# pastes implicitly. @let binds a generation-time name to an expression;
# {{$getter}} inlines it. Scope: enclosing block. In expressions, bare
# names are always literal tokens (like get_ above); variables are the
# $-prefixed ones.

@macro FIRST_TWO($xs: seq<token>)
{{$xs[0]}}, {{$xs[1]}}
@endmacro

# ── 4. Default tail arguments (positional, arity dispatch) ──────────
@macro LOG($msg, $level = INFO, $out = stderr)
fprintf({{$out}}, "[" #level "] %s\n", {{$msg}});
@endmacro
#   LOG(m) / LOG(m, WARN) / LOG(m, WARN, stdout) all valid.
#   Default may be empty:  @macro ATTR(name, qualifiers = )

# ── 5. Named arguments — any order, any subset ──────────────────────
@macro MAKE_WIDGET($name, named $WIDTH = 100, named $HEIGHT = 50, named $FLAGS = )
struct widget {{$name}} = { {{$WIDTH}}, {{$HEIGHT}}, {{$FLAGS}} };
@endmacro
#   MAKE_WIDGET(w1)
#   MAKE_WIDGET(w2, HEIGHT(80))
#   MAKE_WIDGET(w3, FLAGS(BOLD), WIDTH(20))

# ── 6. Maybe-paren stripping (comma protection) ─────────────────────
@macro PAIR($p: tuple<$a, $b>)
S{ {{remove_parens($p.$a)}} | {{$p.$b}} }
@endmacro
#   PAIR((a, b))                → S{ a | b }
#   PAIR(((pair<int,int>), b))  → S{ pair<int,int> | b }

# ── 7. Variadic parameter + is_paren() ──────────────────────────────
@macro FOO($items: variadic)
S{ @join $items as $it with ", ": @if is_paren($it) {{$it}} @else ({{$it}}, omit) @end@end }
@endmacro
#   FOO(a, (b,c), d)  →  S{ (a, omit), (b, c), (d, omit) }

# ── 8. Conditionals: len() tests and integer equality ───────────────
@macro CTOR($name, $args: seq<tuple<$type, $argname>>)
@if len($args) == 1
  explicit_single_arg_init({{$name}})
@else
  {{concat($name, _init)}}(@join $args with ", ": {{$argname}}@end)
@end
@endmacro

# ── 9. Per-file pragmas (override CLI) ──────────────────────────────
@pragma pp_prefix  MYLIB_PP_
@pragma pp_include "mylib/preprocessor.hpp"
```

### Language rules
- Body is raw C text; `@`-directives for control flow; `{{expr}}` interpolation.
- Loops: `@for ($a, $b) in $xs` (tuple unpack) or `@for $x in $xs` / `@join $xs as $x with "sep"`.
- Loops nest up to 4 deep. Verified empirically: SEQ_FOR_EACH cannot
  re-enter itself (the _R forms do not help), so the outer level uses
  SEQ_FOR_EACH and inner levels use BOOST_PP_REPEAT (auto-reentrant, 3
  dimensions) with SEQ_ELEM(n, seq) element access; the seq plus free outer
  variables ride slot 0+ of the REPEAT data tuple. Depth 5+ is rejected
  with a clear error. @if nests freely at any depth.
- Per macro: required positional params + at most ONE of {tail defaults, named
  section, variadic}. Variadic must be last.
- Defaults may be empty (`$x = ` / `named $X = `): the parameter substitutes
  as zero tokens. Bare `named $X` (no `=`) is shorthand for the empty default;
  positional params have no such shorthand (bare `$x` means required). Empty
  values also pass at call sites: `KEY()` for named args, and an explicit
  empty trailing argument (`M(a,)`) still counts in the arity scan, selecting
  the higher arity with an empty value instead of the default.
- Seq/variadic args must be non-empty at C call sites (Boost.PP limitation, documented).
- Conditions: `len($seq) == N` (and <, >, etc.), integer equality (0–256 range),
  `is_paren($x)`, `is_empty($x)`.
- Unbounded tuples (`tuple<T...>`; bare `tuple` = `tuple<token...>`): call
  site `(a, b, c)`, `()` = zero elements, ≤64 elements. Support `len`,
  `[i]`, iteration/unpacking, `is_empty`; no named access. Allowed as
  parameter, seq element, and variadic element types.
- Hybrid tuples (`tuple<n1, .., nk, T...>`): named head + unbounded tail.
  Named access reads the head (`TUPLE_ELEM`, ungated); iteration/`len`/
  `is_empty`/`[i]` are tail-scoped via a generated 2-define extraction
  (`TL<k>(t) = TL<k>_I t`, `TL<k>_I(f1..fk, ...) = (__VA_ARGS__)`) whose
  result is a plain unbounded tuple — all VarTupleT codegen reuses. At
  least k elements required at the call site; head+tail ≤64.
- Call-site caveats (documented in README): args with bare commas must be
  parenthesized; named-arg keywords must not be `#define`d at the call site.

## Codegen mapping (DSL → Boost.PP)

All `BOOST_PP_` occurrences below use the configured prefix
(`@pragma pp_prefix`, default `BOOST_PP_`). Generated helpers are namespaced `UNCURSED_PP_<MACRO>_<KIND>`.

| Construct | Generated code |
|---|---|
| `@for` over seq | no free outer vars: consumption chain `UNCURSED_PP_<M>_CH<n>_k` (body inlined, ~2 expansions/element; size-class table picks chain vs `SEQ_FOR_EACH` fallback above `loop_chain_limit`); otherwise helper `UNCURSED_PP_<M>_EACHn(r,d,e)` + `BOOST_PP_SEQ_FOR_EACH` |
| `@join ... with sep` | identity comma joins: table-driven `BOOST_PP_SEQ_ENUM`; other no-free-var joins: consumption chains with the separator baked between members; otherwise `BOOST_PP_SEQ_FOR_EACH_I` + `BOOST_PP_COMMA_IF(i)` for `","`, `BOOST_PP_IF(i, UNCURSED_PP_<M>_SEPn, BOOST_PP_EMPTY)()` for other seps |
| tuple named access | direct AP/BODY parameter when the tuple is unpacked (loops, spread tuple params); `BOOST_PP_TUPLE_ELEM(idx, x)` otherwise (whole-tuple use, name collisions) |
| seq index `xs[k]` | `BOOST_PP_SEQ_ELEM(k, xs)` |
| `@if/@else` | branch bodies emitted as separate helper macros, selected by `BOOST_PP_IIF(cond, THEN, ELSE)` then invoked — branch text may contain commas |
| `len($xs) == n` etc. | `BOOST_PP_EQUAL(BOOST_PP_SEQ_SIZE(xs), n)` for ==/!=; relationals compile to saturating `BOOL(DEC^k(lhs))` chains with branch swap for </<= (the LESS/GREATER family hides a WHILE-based SUB) |
| `is_paren($x)` | `BOOST_PP_IS_BEGIN_PARENS(x)` |
| `is_empty($x)` | `BOOST_PP_IS_EMPTY(x)`; on unbounded tuples via the shared `ISNIL(x) = IS_EMPTY x` probe (argument expands first, then its own parens become the call — safe for computed values) |
| unbounded-tuple loop/`len` | emptiness gate `IIF(ISNIL(t), NIL/0, ...)` (selected-then-invoked, like @if branches) around `TUPLE_TO_SEQ(t)` + the normal seq machinery / `TUPLE_SIZE(t)`; `()` iterates zero times and measures 0 |
| `remove_parens(x)` | `BOOST_PP_REMOVE_PARENS(x)` |
| `concat(a, b, ...)` | nested `BOOST_PP_CAT(a, BOOST_PP_CAT(b, ...))` |
| `stringize(x)` | `BOOST_PP_STRINGIZE(x)` (stringizes computed tokens; plain `#` only works on direct macro params) |
| loop free vars | outer params referenced in a loop body ride FOR_EACH's `d` slot (one var: `d` itself; several: a tuple in `d`) |
| `@let $name := expr` | generation-time binding; inlined at each use site |
| `named variadic K = d` | keyword value may contain bare commas: `SET_K(...) slot, (__VA_ARGS__)` re-wraps, interpolation auto-`REMOVE_PARENS` — net effect: verbatim value passthrough |
| tail defaults | arity chain `UNCURSED_PP_<M>_1 → ..._N` filling defaults + a per-macro max-arity size scan (`OVERLOAD`'s 65-slot scan is ~2x slower) |
| named args | setter dispatch: one `SET_<KW>` per keyword; each `KW(value)` arg pastes onto `SET_` and names its own slot; arity-specific setter chains apply slot updates via generated per-slot replacers (`PUT_<n>`, spread through `KW_SPREAD`) — no fold, no `TUPLE_REPLACE`/`WHILE`; a bounded max-arity size scan dispatches arity (no `OVERLOAD`). Shared `KW_SPREAD` lives in the companion runtime header (`@pragma runtime_name`). Unknown keywords are compile errors, not silent defaults |
| variadic param | `BOOST_PP_VARIADIC_TO_SEQ(__VA_ARGS__)`, then treated as seq |

Efficiency stance: prefer `IIF` over `IF`, keep helper indirection ≤2 deep, no
deferred-expansion tricks; only the outermost loop uses (non-reentrant)
FOR_EACH — inner levels are REPEAT-based, trading O(n) SEQ_ELEM access per
element for reentrancy.

### Helper deduplication / factoring pass

The emitter doesn't print helpers directly; it first builds an IR of needed
helpers (loop-item ops, @if branch bodies, separators), each as a body template
with holes (loop variable slots, constant-token slots). A collapse pass over
the whole output file then:

1. **Exact merge** — helpers with identical normalized bodies (same shape, same
   constants) share one definition regardless of which macro needed them.
2. **Parameterized merge** — loop helpers whose bodies match after abstracting
   ONE constant token (≥2 users) collapse into one shared helper; the constant
   rides through FOR_EACH's otherwise unused `d` argument directly. If sharing
   would require extra machinery (>1 differing constant → tuple in `d` +
   `TUPLE_ELEM` reads), do NOT merge — simplicity of generated code wins, e.g.:

   ```c
   /* from decls.uncursed: DECLARE_INTS emits "int e;", DECLARE_FLOATS
      emits "float e;" → one helper */
   #define UNCURSED_PP_DECLS_H1(r, d, e) d e;
   #define DECLARE_INTS(xs)   BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_DECLS_H1, int, xs)
   #define DECLARE_FLOATS(xs) BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_DECLS_H1, float, xs)
   ```

Shared helpers are named `UNCURSED_PP_<FILESTEM>_H<n>` in deterministic
(first-use) order so golden files stay stable; the file-stem segment keeps
independently generated headers from colliding when included together in one
translation unit. The pass never fires for a single user, never makes
the generated code more indirect than the unshared version (beyond the `d`
pass-through), and no fancier unification than single-token abstraction is
attempted (keep it simple).

Text splicing: `#define` bodies are single logical lines using `\`-continuations
mirroring template line structure; segments joined with single spaces. uncursed-pp
never token-pastes implicitly — `concat(a, b, ...)` is the explicit paste,
compiling to nested `BOOST_PP_CAT` calls.

## Generated header shape

```c
/* Generated by uncursed-pp from <source>.uncursed — do not edit. */
#pragma once
/* granular #includes derived from the primitives actually used, e.g.
   <boost/preprocessor/seq/for_each.hpp> — or the single
   @pragma pp_include header when configured */
...macros...
```

## Architecture & project layout

Pipeline: source → line-level pass (pragmas, macro headers, body/directive
lines, `@endmacro` matching; builds a line map for errors) → lark mini-grammars parse
the structured fragments (macro signatures, directive lines, `{{expr}}`
contents) → dataclass AST → semantic checks → emitter → header text.
Rationale: raw C body text makes a single whole-file grammar awkward; the
two-level split keeps the lark grammars tiny and error positions exact.

```
mise.toml                 # [tools] python = "3.12", uv = "latest"
pyproject.toml            # deps: lark; [project.scripts] uncursed-pp = "uncursed_pp.cli:main"
src/uncursed_pp/
  nodes.py                # AST dataclasses: MacroDef, Param, TokenT/SeqT/TupleT/VariadicT,
                          #   Text, Interp, ForEach, Join, If, LenCmp, IntEq, IsParen,
                          #   VarRef, ElemAccess, RemoveParens, Pragma
  grammar.lark            # signature / directive / expression mini-grammars
  parser.py               # line pass + lark transformers → AST (positions attached)

  emitter.py              # AST → helper IR + macro bodies → C header text (prefix-aware)
  collapse.py             # helper dedup/factoring pass over the helper IR
  cli.py                  # argparse: input.uncursed [-o out.h] (config via @pragma)
tests/
  test_<feature>.py       # one file per feature: AST + error-path checks
  test_golden.py          # exact golden-header comparison (golden/**/)
  test_e2e_specs.py       # #? specs through real `cc -E` (vendored boost)
  golden/<category>/*.uncursed + *.h
examples/{features,combined}/*.uncursed + *.h  # human-facing walkthroughs, see examples/README.md
```

## Semantic checks (each a clear file:line:col error)

undefined variable refs; duplicate macro/param names; iterating a non-seq/
non-variadic; unpack arity ≠ tuple arity; unknown tuple field name; tuple/seq
index out of range; loop nesting; variadic not last; mixing tail-defaults/
named/variadic; default-less param after defaulted one; unknown pragma.

## Testing & verification

- Unit tests per stage; emitter compared against golden `.h` files.
- Integration: compile each golden header + a snippet invoking the macro with
  `cc -E -P`, normalize whitespace, assert expansion (e.g. `DECLARE_FIELDS(((int,x))((float,y)))`
  → `int x; float y;`). Skipped automatically if `boost/preprocessor.hpp` isn't
  findable (probe `cc -E` on an include stub; honor `BOOST_INCLUDE_DIR` env).
- Commands: `mise install && uv sync` → `uv run pytest` →
  `uv run uncursed-pp examples/features/01-for.uncursed -o /tmp/example.h && cc -E -P /tmp/check.c`.

## Implementation order (each step ends green)

1. Scaffold: mise.toml, pyproject, package skeleton, CLI stub, pytest wired.
2. Core path: parser + AST + emitter for plain macros, `{{$var}}`, `@for` with
   tuple unpack → DECLARE_FIELDS works end-to-end; golden test + first `cc -E` test.
3. `@join` (inline + line form), element access, `remove_parens`.
4. `@if/@else` with `len()`/int-equality/`is_paren` conditions (branch-helper codegen).
5. Tail defaults (OVERLOAD chain), then named args (probes + FOLD_LEFT).
6. `variadic` param type.
7. Helper collapse pass (exact merge, then `d`-parameterized merge) — golden
   files updated to shared-helper output; unit tests for merge/no-merge cases.
8. `@pragma` configuration plumbed through emitter (pragma-only; no CLI flags).
9. Examples, README (call-site caveats), full integration suite.
