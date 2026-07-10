# How each feature compiles — and where the sharp edges are

A field guide to reading the headers uncursed-pp generates: what every DSL
feature turns into, why, and the edge cases to keep in mind. Companion to
[guide.md](guide.md) (language reference), [design.md](design.md) (spec),
[loop-chains.md](loop-chains.md) (loop codegen in depth), and
[optimization.md](optimization.md) (why the codegen looks like this).

## Reading a generated header

Naming conventions (all under your `helper_prefix`, default `UNCURSED_PP_`):

| Name | Role |
|---|---|
| `<M>_AP<n>` | tuple unpacker, applied to an element by juxtaposition (`AP e`) |
| `<M>_EACH<n>` | `SEQ_FOR_EACH`/`REPEAT` loop body |
| `<M>_CH<n>_<k>` | consumption-chain member for seq length k |
| `<M>_SMALL/BIG/PICK<n>` | chain-vs-fallback size dispatch |
| `<M>_SEP<n>` | non-comma join separator |
| `<M>_THEN/ELSE<n>` | `@if` branch bodies |
| `<M>_SET_<KW>`, `_STEP1`, `_PUT_<i>`, `_BODY`, `_<arity>` | named-argument machinery |
| `<M>_BODY1(_D)` | spread-tuple-parameter body |
| `<M>_CAT<k>` | k-ary paste for `concat()` with ≥4 args |
| `<FILESTEM>_H<n>` / `_HC<n>_` | helpers shared across macros by the collapse pass |

Each macro's block starts with its `.uncursed` source as a comment, and the
`#include` list is exactly the set of Boost.PP headers the emitted
primitives need (nothing else).

## Loops (`@for`)

Tuple elements unpack through an `AP` macro applied by juxtaposition —
`AP e` where `e` is the parenthesized tuple — so each field is a direct
macro parameter instead of a per-use `BOOST_PP_TUPLE_ELEM` dispatch.
Iteration itself is a consumption chain with a `SEQ_FOR_EACH` fallback
(see [loop-chains.md](loop-chains.md)).

Loops that reference **outer parameters** pass them through `FOR_EACH`'s
spare data slot: one variable travels as `d` itself, several as a tuple
read back with `TUPLE_ELEM(i, d)`.

**Sharp edges**
- *Name capture*: names bound in scope — macro parameters, `@for` unpack
  names, tuple field names — compile to macro parameters, so they
  substitute **anywhere in the raw C body text**, not just inside `{{...}}`.
  The template-side `$` prefix is stripped in generated code (a `$suite`
  variable becomes a plain `suite` parameter), so the hazard is about the
  PLAIN name: don't reuse a bound name as an ordinary C identifier in the
  same scope — or set `@pragma arg_prefix u_` to namespace EVERY generated
  parameter (user params and harness slots like `r`/`d`/`e` alike), which
  removes the hazard entirely at the cost of noisier generated code.
- Seqs cap at 256 elements (`BOOST_PP_LIMIT_SEQ`) and must be non-empty.
- Seq-of-tuples call sites need double parens: `((int, x))((float, y))` —
  or declare the parameter `variadic<tuple<...>>` for single-paren calls.

## Nested loops

The outer level uses `SEQ_FOR_EACH`; inner levels use `BOOST_PP_REPEAT`
(auto-reentrant, 3 dimensions → max 4-deep nesting) with `SEQ_ELEM(n, seq)`
element access. The seq and any outer values travel in a data tuple.

**Sharp edges**
- Inner iteration is O(n²) in the element count (`SEQ_ELEM` walks the seq
  each time) — fine for the usual 2–16 elements, noticeable at 100+.
  A verified O(n) alternative exists but costs a chain family per level;
  rejected on the header-size budget (see optimization.md, round 4).
- **Never invoke another looping generated macro from inside a loop body.**
  `SEQ_FOR_EACH` cannot re-enter itself ("blue paint"): the inner call
  silently emits unexpanded `BOOST_PP_SEQ_FOR_EACH` text. uncursed-pp
  cannot see call sites to guard this. Sequential calls outside loops
  compose fine (that's how `REFLECT`-style fan-out works).

## Joins (`@join`)

Comma separators compile to `COMMA_IF(i)`; other separators get a tiny
`SEP` helper selected by `IF(i, SEP, EMPTY)()`. The identity comma join —
body is exactly the element — compiles to a bare `BOOST_PP_SEQ_ENUM`.
Chains bake the separator between members.

## Conditionals (`@if` / `@else`)

Branch bodies become separate helper macros selected by `BOOST_PP_IIF` and
*then* invoked — so branch text may contain commas freely. Each branch
helper takes the union of both branches' free variables as parameters.

Conditions:
- `==` / `!=` → `BOOST_PP_EQUAL` / `NOT_EQUAL` (table-driven, cheap).
- `<` `<=` `>` `>=` → a saturating `BOOL(DEC^k(lhs))` chain (the literal is
  known at generation time; Boost's `LESS`/`GREATER` hide a `WHILE` loop
  that measured ~27× slower). `<`/`<=` swap the branch order; `x < 0` and
  `x >= 0` constant-fold.
- `is_paren($x)` → `IS_BEGIN_PARENS`; `is_empty($x)` → the emptiness probe.

**Sharp edges**
- All compared magnitudes (including `len()`) live in 0–256.
- Very large relational literals inline k nested `DEC`s into the condition
  (~2 expansions each). A table-based alternative for k ≥ 12 was verified
  but rejected on header size (optimization.md).
- `is_empty()`'s probe can misdetect a value that is itself the *name of a
  function-like macro* (it expands during probing) — same family as the
  named-argument keyword rule below.

## Expressions

| DSL | Emitted | Notes |
|---|---|---|
| `concat(a, b)` / 3 args | nested `BOOST_PP_CAT` | |
| `concat(...)` ≥ 4 args | one generated k-ary paste `<M>_CAT<k>` | 2 expansions total |
| `stringize(x)` | `BOOST_PP_STRINGIZE` | works on computed tokens; plain `#` only works on direct parameters |
| `remove_parens(x)` | `BOOST_PP_REMOVE_PARENS` | conditional by necessity: the value may legitimately be bare |
| `len($xs)` | `BOOST_PP_SEQ_SIZE` | O(n) paste chain, cheap |
| `{{$f.$name}}` on a spread tuple param | the field's own parameter | zero-cost; falls back to `TUPLE_ELEM` when the whole tuple is also used or names collide |

`@let` bindings are generation-time: the rendered expression is inlined at
each use site. A `@let` bound to an inline `@join`/`@if` renders its helper
machinery once, but the *call* re-executes per use site.

## Tail defaults

An arity chain (`<M>_1` … `<M>_N`, each filling the remaining defaults)
dispatched by a **max-arity-bounded size scan** — a generated
`<M>_SIZE(...)`/`_DISPATCH` pair, not `BOOST_PP_OVERLOAD` (whose 65-slot
scan measured 2× slower). Macros using only this feature often need **no
boost includes at all**.

**Sharp edges**
- Defaults/named macros need ≥ 1 required parameter (C can't overload on
  zero arguments).
- Default values must be single comma-free, paren-free token sequences.
- `#param` stringize works inside default-arity bodies (parameters are
  real), unlike loop bodies (use `stringize()` there).

## Named arguments

Each keyword argument dispatches itself: `STEP1` pastes `SET_ ## WIDTH(20)`,
the setter emits its slot index + value, and a generated per-slot
`PUT_<i>` rebuilds the bare state list with that slot replaced. Arities
nest setters directly (`BODY_D(name, STEP1(e2, STEP1(e1, defaults)))`) —
no fold, no state tuple. `named variadic` setters accept and re-wrap
bare-comma values.

**Sharp edges**
- Keyword names must not be `#define`d at the call site — they'd expand
  before dispatch.
- A **misspelled keyword is a hard preprocessor error** ("BODY requires N
  arguments") — this is a tested contract. A faster "curried setter" form
  was verified and rejected because it silently emitted garbage here
  (optimization.md, round 4).
- Repeated keyword: the last occurrence wins.
- Plain `named` values are one macro argument; `FLAGS(a, b)` errors naming
  the setter. Comma values need `named variadic` or caller parens.

## Variadic & unbounded tuples

`variadic` params bind to `BOOST_PP_VARIADIC_TO_SEQ(__VA_ARGS__)` — the
body sees an ordinary seq, so all loop/`len()`/indexing machinery applies
unchanged. Unbounded tuples (`tuple<T...>`, bare `tuple`) accept `()` and
cap at 64 elements (`BOOST_PP_VARIADIC_SIZE`).

**Sharp edges**
- The variadic conversion text is inlined at *every* reference to the
  parameter — measured harmless (consuming loops dominate 40:1), but it's
  why generated defines can look repetitive.
- Unbounded-tuple emptiness probing shares the function-like-macro-name
  edge with `is_empty()` above.

## The collapse pass

After emission, identical helpers across a file merge into shared
`<FILESTEM>_H<n>` definitions; loop helpers differing by exactly **one**
constant token merge with the constant riding the data slot; chain families
merge only as whole families (`_HC<n>_` prefix). Shared names are numbered
in first-use order and namespaced by file stem so two generated headers
can't collide in one TU.

**Sharp edge (for contributors):** names assembled by `CAT` at C time —
chain-family prefixes, setter families — are invisible to whole-name
renames. Any pass that renames helpers must operate on the assembling
*prefix*, and every feature-pair interaction needs at least one
through-the-compiler test with both enabled. (Both lessons were paid for;
see the postmortems in optimization.md.)

## The runtime companion

`KW_SPREAD` (tuple unpacking) and the default `LE16` size table live in a
shared `uncursed_pp_runtime.h`, written next to the output with
`--emit-runtime`. Generated headers include it only when they use those
utilities; several headers share one copy, and identical re-definitions
are benign if two configurations collide.
