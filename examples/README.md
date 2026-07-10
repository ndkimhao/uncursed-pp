# Examples

Human-facing, single-feature examples of the uncursed-pp DSL. Every
`.uncursed` file here is also a golden test: its generated `.h` is
committed next to it, and the `#? INVOCATION` / `#=> expansion` comments
are verified token-exact through the real C preprocessor by the test
suite. What you read is what actually happens.

Read `features/` in order — each file teaches one feature and states the
call-site rules that apply to it:

| # | Feature |
|---|---------|
| 01 | `@for` — loop over a seq |
| 02 | `@for (a, b)` — tuple destructuring (and the double-paren rule) |
| 03 | nested loops (4-deep limit) |
| 04 | `@join` — separators between elements |
| 05 | `@if` / `@else` with `len()` |
| 06 | `is_paren()` — dispatch on parenthesization |
| 07 | `concat()` — explicit token pasting |
| 08 | `stringize()` — C strings from computed tokens |
| 09 | `remove_parens()` — the comma-protection idiom |
| 10 | `@let` — generation-time bindings |
| 11 | tail defaults — optional trailing arguments |
| 12 | named arguments — any order, any subset |
| 13 | `named variadic` — keywords accepting bare commas |
| 14 | `variadic` — comma-separated call sites |
| 15 | `@pragma` — in-template configuration |
| 16 | codegen: helper collapse (read the .h!) |
| 17 | codegen: adjacency never pastes |
| 18 | unbounded tuples — `tuple<T...>`, `()` means zero elements |
| 19 | hybrid tuples — named head fields + unbounded tail |

`combined/` shows features composing into realistic artifacts: a bitflag
system, a finite state machine, and a unit-test registry — each driven
by a single source-of-truth list.

Compile one yourself:

```sh
uv run uncursed-pp examples/features/01-for.uncursed -o /tmp/out.h
cat /tmp/out.h
```

The generated header embeds its own source as a comment, so a `.h` is
readable standalone. Headers that need shared utilities `#include` a
small companion runtime header, committed alongside here; when compiling
your own templates, pass `--emit-runtime` to write it next to the output.
