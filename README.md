# uncursed-pp

A compiler from a readable template DSL to C preprocessor macros built on
[Boost.Preprocessor](https://www.boost.org/doc/libs/latest/libs/preprocessor/doc/index.html).
You describe a macro like a web render template; uncursed-pp emits the cursed
`BOOST_PP_` machinery. Loops and conditionals in the *generated* macros run at
**C compile time** — call sites pass real variable-length data.

```text
macro DECLARE_FIELDS(fields: seq<tuple<type, name>>)
@for (type, name) in fields
  {{type}} {{name}};
@end
end
```

```c
/* generated — each macro's block carries its .uncursed source as a comment */
/* uncursed-pp source:
 * macro DECLARE_FIELDS(fields: seq<tuple<type, name>>)
 * ...
 */
#define UNCURSED_PP_DECLARE_FIELDS_AP1(type, name) type name;
#define UNCURSED_PP_DECLARE_FIELDS_EACH1(r, d, e) UNCURSED_PP_DECLARE_FIELDS_AP1 e
#define DECLARE_FIELDS(fields) BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_DECLARE_FIELDS_EACH1, ~, fields)

/* usage — expands at C compile time */
DECLARE_FIELDS(((int, x))((float, y)))   /* → int x; float y; */
```

## Install & use

```sh
make setup          # mise install + uv sync + vendored Boost.PP clone
make test           # pytest incl. real `gcc -E` e2e specs (vendored boost)
make typecheck      # mypy --strict

uv run uncursed-pp input.uncursed -o output.h
```

Golden templates in `tests/golden/` are self-testing — each carries spec
comments that the suite discovers and verifies through the real preprocessor:

```text
#?  MAKE_WIDGET(w3, FLAGS(BOLD), WIDTH(20))
#=>     struct widget w3 = { 20, 50, BOLD };
```

Configuration lives in the template itself (`@pragma <name> <value>` lines),
so a header regenerates identically from the source file alone — the CLI
takes only the input path and `-o`:

| Pragma | Default | Meaning |
|---|---|---|
| `pp_prefix` | `BOOST_PP_` | prefix of the preprocessor library's macros |
| `pp_include` | *(granular)* | single header to include instead of granular ones |
| `pp_include_dir` | `boost/preprocessor` | root of the granular usage-derived includes |
| `helper_prefix` | `UNCURSED_PP_` | prefix of generated helper macros |
| `runtime_name` | `<helper_prefix>_runtime.h` | filename of the shared runtime header |
| `include` | — | extra `#include` for the generated header (repeatable) |

```text
@pragma pp_prefix  MYLIB_PP_
@pragma pp_include_dir boost_foo/preprocessor
@pragma include "myproj/types.h"
@pragma include <stdio.h>
```

## The language

A `.uncursed` file holds `#` comments, optional `@pragma` lines, and macro
definitions. The body is raw C text; control flow uses `@`-directives;
`{{expr}}` interpolates. See `examples/example.uncursed` for a feature tour.

| Feature | Syntax |
|---|---|
| Types | `token` (default), `seq<T>`, `tuple<name, ...>`, `variadic` / `variadic<T>` |
| Loop | `@for (a, b) in xs` / `@for x in xs` ... `@end` |
| Join | `@join xs with ", ": body @end` (inline) or block form; `as x` binds the element |
| Conditional | `@if len(xs) == 1` / `@if is_paren(x)` ... `@else` ... `@end` (ops: `== != < > <= >=`) |
| Element access | `{{t.field}}` (tuple, by name), `{{xs[0]}}` (seq, by index) |
| Paste | `{{concat(get_, f.name)}}` → `BOOST_PP_CAT` — pasting is never implicit |
| Stringize | `{{stringize(f.name)}}` → `BOOST_PP_STRINGIZE` — works on computed tokens |
| Strip parens | `{{remove_parens(x)}}` — strips one layer iff present |
| Binding | `@let g := concat(get_, f.name)` — generation-time, block-scoped; also binds an inline `@join`/`@if` for reuse |
| Tail defaults | `macro LOG(msg, level = INFO, out = stderr)` — arity dispatch |
| Named args | `macro W(name, named WIDTH = 100)` — call `W(n, WIDTH(20))`, any order/subset |
| Variadic | `macro F(items: variadic)` — call `F(a, (b,c), d)`; body sees a seq. `variadic<tuple<t, n>>` gives single-paren tuple call sites: `F((int, x), (float, y))` |

Within a loop over `seq<tuple<...>>`, the tuple's element names are bound
automatically (`@join args with ", ": {{type}} {{argname}}@end`). Loop bodies
may reference outer parameters freely — they travel through `FOR_EACH`'s data
slot. See `tests/golden/compose/reflect.uncursed` for a worked example: a
reflection system where one field list generates a struct, a name/type/offset
metadata table, and a debug printer.

Identical generated helpers are deduplicated across the file into shared
`UNCURSED_PP_<FILESTEM>_H<n>` macros; loop bodies differing by one constant
token share a helper with the constant passed through `FOR_EACH`'s data slot.

Common utilities (currently the `KW_SPREAD` tuple-unpacking helper) are not
inlined: headers that need them `#include "uncursed_pp_runtime.h"`, a small
companion file uncursed-pp writes next to the output. Multiple generated headers
share the one runtime file. Its name defaults to `<helper_prefix>_runtime.h`
and is customizable via `@pragma runtime_name "acme_common.h"`.

## Call-site rules (C is still C)

- **Seqs of tuples need double parens**: `F(((int, x))((float, y)))` — each
  seq element is parenthesized, and the element itself is a tuple.
- **Bare commas break argument counting**: wrap comma-containing values in
  parens and unwrap with `remove_parens()` in the template. For named
  arguments there's an opt-in: declare the param `named variadic` and the
  keyword accepts bare commas — `COLORS(red, green, blue)` — with the value
  passing through verbatim (plain `named` keeps the strict one-token value;
  `FLAGS(a, b)` there is a compile error naming the keyword's setter).
- **Seq/variadic arguments must be non-empty** — Boost.PP seqs cannot be empty —
  and cap at **256 elements** (`BOOST_PP_LIMIT_SEQ`).
- **Named-argument keywords** (`WIDTH`, ...) must not be `#define`d at the call
  site, or they expand before detection.
- **Defaults/named macros need ≥1 required parameter** — C can't overload on
  zero arguments.
- `len()` comparisons ride `BOOST_PP_EQUAL`/friends: magnitudes limited to
  0–256.
- Loops nest up to **4 deep**: the outer level compiles to `SEQ_FOR_EACH`
  and inner levels to `BOOST_PP_REPEAT` (3 auto-detected dimensions) with
  `SEQ_ELEM` indexing — deeper nesting is a compile error. `@if` nests
  freely at any depth. Don't call another looping generated macro from a
  loop body (uncursed-pp can't see call sites to guard it).

## Architecture

`parser.py` (line pass + lark mini-grammars in `grammar.lark`) → dataclass AST
(`nodes.py`) → emitter with typed environments (`emitter.py`) → helper
dedup/factoring (`collapse.py`) → header text. Design spec: `docs/design.md`.
