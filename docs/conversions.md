# Converting between shapes — seq, tuple, variadic, comma lists

uncursed-pp has three variable-count shapes and one "bare" form:

| Shape | Declared as | Call-site / value form |
|---|---|---|
| seq | `seq<T>` | `(a)(b)(c)` — non-empty, ≤256 elements |
| unbounded tuple | `tuple` / `tuple<T...>` (hybrid: `tuple<$n1, .., T...>`) | `(a, b, c)` — `()` = zero elements, ≤64 |
| variadic | `$rest: variadic` / `variadic<T>` | `a, b, c` as trailing macro arguments |
| bare comma list | *(not a declared type)* | `a, b, c` spliced into surrounding text |

## Automatic conversions

- **variadic → seq**: the macro body already sees a variadic parameter as
  a `seq<T>` (`BOOST_PP_VARIADIC_TO_SEQ`). Passing it on — `INNER({{$items}})`
  — hands the *converted* seq to any seq-typed macro; this is how
  `REFLECT`-style composition works.
- **hybrid tuple → tail**: every tail-scoped operation (iteration, `len()`,
  `is_empty()`, `[i]`, `to_seq()`) extracts the unbounded tail behind the
  named head fields automatically.

## Explicit conversions

| Conversion | Write | Result |
|---|---|---|
| tuple → seq | `to_seq($row)` | `(a, b, c)` → `(a)(b)(c)`; result is fully seq-typed (loop it, index it, pass it to seq macros). Identity on seq-typed values. Hybrids convert their tail. |
| seq → tuple | `to_tuple($xs)` | `(a)(b)(c)` → `(a, b, c)`; result is fully tuple-typed (`len`, `[i]`, `is_empty`, iteration). Identity on tuple-typed values. |
| tuple → bare commas | `{{remove_parens($row)}}` | `(a, b, c)` → `a, b, c` — the tuple's parens are its only wrapper |
| seq → bare commas | `@join $xs with ", ": {{$x}}@end` | the identity comma join (compiles to the cheap `BOOST_PP_SEQ_ENUM`) |
| anything → parenthesized | `({{$x}})` | plain text — parens are just text |

Both functions compose with `@let` for reuse:

```text
@let $s := to_seq($row)
@for $x in $s
  ...
@end
```

## Rules and sharp edges

- **Seqs cannot be empty**, so `to_seq(())` is the same call-site error as
  any other empty seq — convert only when the tuple is known non-empty
  (guard with `@if is_empty($row)` if it may be).
- `to_tuple`'s result obeys tuple semantics everywhere, including the
  64-element cap (vs 256 for seqs).
- There is no variadic *value* to convert TO: "variadic" is a call-site
  arrangement, not a value shape. To emit a bare comma list, use
  `remove_parens` (from a tuple) or an identity join (from a seq).
- Codegen: `to_seq` is one `BOOST_PP_TUPLE_TO_SEQ` (a size-CAT table
  dispatch), `to_tuple` one `BOOST_PP_SEQ_TO_TUPLE` (a `SEQ_ENUM` in
  parens) — both cheap relative to any consuming loop; per
  [optimization.md](optimization.md), don't bother hoisting them.

Worked example: [`examples/features/21-conversions.uncursed`](../examples/features/21-conversions.uncursed);
golden: `tests/golden/expressions/convert.uncursed`.
