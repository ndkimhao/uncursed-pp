# cursedpp — a template DSL compiled to Boost.Preprocessor C macros

## Context

Writing Boost.PP macro machinery by hand (FOR_EACH helpers, overload dispatchers,
keyword-arg probes) is unreadable and error-prone. cursedpp is a Python compiler:
you write macros in a readable, web-template-style DSL (`.cursed` files); it emits
a C header of `#define`s built on Boost.PP, so the macros consume variable data at
C compile time. Greenfield project in `/home/hao/repos/cursedpp` (empty git repo).

## DSL specification (approved sketch)

```text
# Comments start with '#'. A file holds any number of macro definitions.
# Arg types: token (default), seq<T>, tuple<name, ...> (named elems), variadic.

# ── 1. Loop over a seq of tuples ────────────────────────────────────
macro DECLARE_FIELDS(fields: seq<tuple<type, name>>)
@for (type, name) in fields
  {{type}} {{name}};
@end
end
#   DECLARE_FIELDS(((int, x))((float, y)))  →  int x; float y;

# ── 2. Inline @join — separator between items ───────────────────────
macro PROTO(name, args: seq<tuple<type, argname>>)
void {{name}}(@join args with ", ": {{type}} {{argname}}@end);
end
#   PROTO(draw, ((struct ctx *, ctx))((int, flags)))
#     →  void draw(struct ctx * ctx, int flags);

# ── 3. Element access: named for tuples, indexed for seqs ───────────
macro GETTER(field: tuple<type, name>)
@let getter := concat(get_, field.name)
{{field.type}} {{getter}}(const struct self *s) {
  return s->{{field.name}};
}
end
# concat() is EXPLICIT token pasting (BOOST_PP_CAT); adjacent text never
# pastes implicitly. @let binds a generation-time name to an expression;
# {{getter}} inlines it. Scope: enclosing block. In expressions, a bare
# name resolves to a param/loop var/let if one is in scope, else it is a
# literal token (like get_ above).

macro FIRST_TWO(xs: seq<token>)
{{xs[0]}}, {{xs[1]}}
end

# ── 4. Default tail arguments (positional, arity dispatch) ──────────
macro LOG(msg, level = INFO, out = stderr)
fprintf({{out}}, "[" #level "] %s\n", {{msg}});
end
#   LOG(m) / LOG(m, WARN) / LOG(m, WARN, stdout) all valid.
#   Default may be empty:  macro ATTR(name, qualifiers = )

# ── 5. Named arguments — any order, any subset ──────────────────────
macro MAKE_WIDGET(name, named WIDTH = 100, named HEIGHT = 50, named FLAGS = )
struct widget {{name}} = { {{WIDTH}}, {{HEIGHT}}, {{FLAGS}} };
end
#   MAKE_WIDGET(w1)
#   MAKE_WIDGET(w2, HEIGHT(80))
#   MAKE_WIDGET(w3, FLAGS(BOLD), WIDTH(20))

# ── 6. Maybe-paren stripping (comma protection) ─────────────────────
macro PAIR(p: tuple<a, b>)
S{ {{remove_parens(p.a)}} | {{p.b}} }
end
#   PAIR((a, b))                → S{ a | b }
#   PAIR(((pair<int,int>), b))  → S{ pair<int,int> | b }

# ── 7. Variadic parameter + is_paren() ──────────────────────────────
macro FOO(items: variadic)
S{ @join items as it with ", ": @if is_paren(it) {{it}} @else ({{it}}, omit) @end@end }
end
#   FOO(a, (b,c), d)  →  S{ (a, omit), (b, c), (d, omit) }

# ── 8. Conditionals: len() tests and integer equality ───────────────
macro CTOR(name, args: seq<tuple<type, argname>>)
@if len(args) == 1
  explicit_single_arg_init({{name}})
@else
  {{concat(name, _init)}}(@join args with ", ": {{argname}}@end)
@end
end

# ── 9. Per-file pragmas (override CLI) ──────────────────────────────
@pragma pp_prefix  MYLIB_PP_
@pragma pp_include "mylib/preprocessor.hpp"
```

### Language rules
- Body is raw C text; `@`-directives for control flow; `{{expr}}` interpolation.
- Loops: `@for (a, b) in xs` (tuple unpack) or `@for x in xs` / `@join xs as x with "sep"`.
- Flat only: one loop level per macro; nesting is a cursedpp compile error.
- Per macro: required positional params + at most ONE of {tail defaults, named
  section, variadic}. Variadic must be last.
- Seq/variadic args must be non-empty at C call sites (Boost.PP limitation, documented).
- Conditions: `len(seq) == N` (and <, >, etc.), integer equality (0–256 range),
  `is_paren(x)`.
- Call-site caveats (documented in README): args with bare commas must be
  parenthesized; named-arg keywords must not be `#define`d at the call site.

## Codegen mapping (DSL → Boost.PP)

All `BOOST_PP_` occurrences below use the configured prefix (`--pp-prefix`,
default `BOOST_PP_`). Generated helpers are namespaced `CURSEDPP_<MACRO>_<KIND>`.

| Construct | Generated code |
|---|---|
| `@for` over seq | helper `CURSEDPP_<M>_EACHn(r,d,e)` + `BOOST_PP_SEQ_FOR_EACH` |
| `@join ... with sep` | `BOOST_PP_SEQ_FOR_EACH_I` + `BOOST_PP_COMMA_IF(i)` for `","`; for other seps `BOOST_PP_IF(i, CURSEDPP_<M>_SEPn, BOOST_PP_EMPTY)()` |
| tuple named access | `BOOST_PP_TUPLE_ELEM(idx, x)` (modern 2-arg variadic form) |
| seq index `xs[k]` | `BOOST_PP_SEQ_ELEM(k, xs)` |
| `@if/@else` | branch bodies emitted as separate helper macros, selected by `BOOST_PP_IIF(cond, THEN, ELSE)` then invoked — branch text may contain commas |
| `len(xs) == n` etc. | `BOOST_PP_EQUAL(BOOST_PP_SEQ_SIZE(xs), n)` / `LESS` / `GREATER` |
| `is_paren(x)` | `BOOST_PP_IS_BEGIN_PARENS(x)` |
| `remove_parens(x)` | `BOOST_PP_REMOVE_PARENS(x)` |
| `concat(a, b, ...)` | nested `BOOST_PP_CAT(a, BOOST_PP_CAT(b, ...))` |
| `@let name := expr` | generation-time binding; inlined at each use site |
| tail defaults | arity chain `CURSEDPP_<M>_1 → ..._N` filling defaults + `#define M(...) BOOST_PP_OVERLOAD(CURSEDPP_<M>_, __VA_ARGS__)(__VA_ARGS__)` |
| named args | setter dispatch: one `SET_<KW>(v) slot, v` per keyword; each `KW(value)` arg pastes onto `SET_` and names its own slot, a single `SEQ_FOLD_LEFT` TUPLE_REPLACEs slots in the defaults tuple; arity dispatch via OVERLOAD handles the zero-keyword call; shared KW_PUT utils live in a companion runtime header (name via `--runtime-name` / `@pragma runtime_name`), one copy for all generated headers. Unknown keywords are compile errors, not silent defaults |
| variadic param | `BOOST_PP_VARIADIC_TO_SEQ(__VA_ARGS__)`, then treated as seq |

Efficiency stance: prefer `IIF` over `IF`, keep helper indirection ≤2 deep, no
deferred-expansion tricks; flat-only means non-reentrant FOR_EACH is fine.

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
   /* DECLARE_INTS emits "int e;", DECLARE_FLOATS emits "float e;" → one helper */
   #define CURSEDPP_H1(r, d, e) d e;
   #define DECLARE_INTS(xs)   BOOST_PP_SEQ_FOR_EACH(CURSEDPP_H1, int, xs)
   #define DECLARE_FLOATS(xs) BOOST_PP_SEQ_FOR_EACH(CURSEDPP_H1, float, xs)
   ```

Shared helpers are named `CURSEDPP_H<n>` in deterministic (first-use) order so
golden files stay stable. The pass never fires for a single user, never makes
the generated code more indirect than the unshared version (beyond the `d`
pass-through), and no fancier unification than single-token abstraction is
attempted (keep it simple).

Text splicing: `#define` bodies are single logical lines using `\`-continuations
mirroring template line structure; segments joined with single spaces. cursedpp
never token-pastes implicitly — `concat(a, b, ...)` is the explicit paste,
compiling to nested `BOOST_PP_CAT` calls.

## Generated header shape

```c
/* Generated by cursedpp from example.cursed — do not edit. */
#pragma once
/* granular #includes derived from the primitives actually used, e.g.
   <boost/preprocessor/seq/for_each.hpp> — or the single --pp-include /
   @pragma pp_include header when configured */
...macros...
```

## Architecture & project layout

Pipeline: source → line-level pass (pragmas, macro headers, body/directive
lines, `end` matching; builds a line map for errors) → lark mini-grammars parse
the structured fragments (macro signatures, directive lines, `{{expr}}`
contents) → dataclass AST → semantic checks → emitter → header text.
Rationale: raw C body text makes a single whole-file grammar awkward; the
two-level split keeps the lark grammars tiny and error positions exact.

```
mise.toml                 # [tools] python = "3.12", uv = "latest"
pyproject.toml            # deps: lark; [project.scripts] cursedpp = "cursedpp.cli:main"
src/cursedpp/
  nodes.py                # AST dataclasses: MacroDef, Param, TokenT/SeqT/TupleT/VariadicT,
                          #   Text, Interp, ForEach, Join, If, LenCmp, IntEq, IsParen,
                          #   VarRef, ElemAccess, RemoveParens, Pragma
  grammar.lark            # signature / directive / expression mini-grammars
  parser.py               # line pass + lark transformers → AST (positions attached)
  semantics.py            # checks below
  emitter.py              # AST → helper IR + macro bodies → C header text (prefix-aware)
  collapse.py             # helper dedup/factoring pass over the helper IR
  cli.py                  # argparse: input.cursed [-o out.h] [--pp-prefix] [--pp-include]
tests/
  test_parser.py  test_semantics.py  test_emitter.py   # unit
  golden/*.cursed + *.h                                # golden-file emitter tests
  test_integration.py                                  # real `cc -E`, auto-skip w/o boost
examples/example.cursed
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
  `uv run cursedpp examples/example.cursed -o /tmp/example.h && cc -E -P /tmp/check.c`.

## Implementation order (each step ends green)

1. Scaffold: mise.toml, pyproject, package skeleton, CLI stub, pytest wired.
2. Core path: parser + AST + emitter for plain macros, `{{var}}`, `@for` with
   tuple unpack → DECLARE_FIELDS works end-to-end; golden test + first `cc -E` test.
3. `@join` (inline + line form), element access, `remove_parens`.
4. `@if/@else` with `len()`/int-equality/`is_paren` conditions (branch-helper codegen).
5. Tail defaults (OVERLOAD chain), then named args (probes + FOLD_LEFT).
6. `variadic` param type.
7. Helper collapse pass (exact merge, then `d`-parameterized merge) — golden
   files updated to shared-helper output; unit tests for merge/no-merge cases.
8. `@pragma` + `--pp-prefix`/`--pp-include` plumbed through emitter.
9. Examples, README (call-site caveats), full integration suite.
