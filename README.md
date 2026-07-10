# cursedpp

A compiler from a readable template DSL to C preprocessor macros built on
[Boost.Preprocessor](https://www.boost.org/doc/libs/latest/libs/preprocessor/doc/index.html).
You describe a macro like a web render template; cursedpp emits the cursed
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
/* generated */
#define CURSEDPP_DECLARE_FIELDS_EACH1(r, d, e) BOOST_PP_TUPLE_ELEM(0, e) BOOST_PP_TUPLE_ELEM(1, e);
#define DECLARE_FIELDS(fields) BOOST_PP_SEQ_FOR_EACH(CURSEDPP_DECLARE_FIELDS_EACH1, ~, fields)

/* usage — expands at C compile time */
DECLARE_FIELDS(((int, x))((float, y)))   /* → int x; float y; */
```

## Install & use

```sh
make setup          # mise install + uv sync
make test           # pytest incl. real `gcc -E` integration tests
make typecheck      # mypy --strict

uv run cursedpp input.cursed -o output.h
```

Flags (each also settable per file via `@pragma <name> <value>`):

| Flag | Default | Meaning |
|---|---|---|
| `--pp-prefix` | `BOOST_PP_` | prefix of the preprocessor library's macros |
| `--pp-include` | *(granular)* | single header to include instead of granular ones |
| `--pp-include-dir` | `boost/preprocessor` | root of the granular usage-derived includes |
| `--helper-prefix` | `CURSEDPP_` | prefix of generated helper macros |
| `--runtime-name` | `<helper-prefix>_runtime.h` | filename of the shared runtime header |
| `--include` | — | extra `#include` for the generated header (repeatable) |

```text
@pragma pp_prefix  MYLIB_PP_
@pragma pp_include_dir boost_foo/preprocessor
@pragma include "myproj/types.h"
@pragma include <stdio.h>
```

## The language

A `.cursed` file holds `#` comments, optional `@pragma` lines, and macro
definitions. The body is raw C text; control flow uses `@`-directives;
`{{expr}}` interpolates. See `examples/example.cursed` for a feature tour.

| Feature | Syntax |
|---|---|
| Types | `token` (default), `seq<T>`, `tuple<name, ...>`, `variadic` |
| Loop | `@for (a, b) in xs` / `@for x in xs` ... `@end` |
| Join | `@join xs with ", ": body @end` (inline) or block form; `as x` binds the element |
| Conditional | `@if len(xs) == 1` / `@if is_paren(x)` ... `@else` ... `@end` (ops: `== != < > <= >=`) |
| Element access | `{{t.field}}` (tuple, by name), `{{xs[0]}}` (seq, by index) |
| Paste | `{{concat(get_, f.name)}}` → `BOOST_PP_CAT` — pasting is never implicit |
| Strip parens | `{{remove_parens(x)}}` — strips one layer iff present |
| Binding | `@let g := concat(get_, f.name)` — generation-time, block-scoped |
| Tail defaults | `macro LOG(msg, level = INFO, out = stderr)` — arity dispatch |
| Named args | `macro W(name, named WIDTH = 100)` — call `W(n, WIDTH(20))`, any order/subset |
| Variadic | `macro F(items: variadic)` — call `F(a, (b,c), d)`; body sees a seq |

Within a loop over `seq<tuple<...>>`, the tuple's element names are bound
automatically (`@join args with ", ": {{type}} {{argname}}@end`).

Identical generated helpers are deduplicated across the file into shared
`CURSEDPP_H<n>` macros; loop bodies differing by one constant token share a
helper with the constant passed through `FOR_EACH`'s data slot.

Common utilities (the keyword-argument `KW_PUT` machinery) are not inlined:
headers that need them `#include "cursedpp_runtime.h"`, a small companion file
cursedpp writes next to the output. Multiple generated headers share the one
runtime file. Its name defaults to `<helper-prefix>_runtime.h` and is
customizable via `--runtime-name` or `@pragma runtime_name "acme_common.h"`.

## Call-site rules (C is still C)

- **Seqs of tuples need double parens**: `F(((int, x))((float, y)))` — each
  seq element is parenthesized, and the element itself is a tuple.
- **Bare commas break argument counting**: wrap comma-containing values in
  parens and unwrap with `remove_parens()` in the template.
- **Seq/variadic arguments must be non-empty** — Boost.PP seqs cannot be empty.
- **Named-argument keywords** (`WIDTH`, ...) must not be `#define`d at the call
  site, or they expand before detection.
- **Defaults/named macros need ≥1 required parameter** — C can't overload on
  zero arguments.
- `len()` comparisons ride `BOOST_PP_EQUAL`/friends: magnitudes limited to
  0–256.
- One loop level per macro (v1): no nested loops, no calling another looping
  generated macro from a loop body.

## Architecture

`parser.py` (line pass + lark mini-grammars in `grammar.lark`) → dataclass AST
(`nodes.py`) → emitter with typed environments (`emitter.py`) → helper
dedup/factoring (`collapse.py`) → header text. Design spec: `docs/design.md`.
