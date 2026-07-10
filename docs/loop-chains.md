# Loop chains

`@pragma loop_chain` controls how loops iterate at C-preprocessing time.
This is the single biggest lever on how expensive your generated headers are
to compile — and on how long they are. This page explains both sides of the
trade so you can pick deliberately.

## The two codegen forms

**Chains off** (`@pragma loop_chain off`): a loop compiles to one helper plus
`BOOST_PP_SEQ_FOR_EACH`:

```c
#define UNCURSED_PP_DECL_AP1(type, name) type name;
#define UNCURSED_PP_DECL_EACH1(r, d, e) UNCURSED_PP_DECL_AP1 e
#define DECL(fields) BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_DECL_EACH1, ~, fields)
```

Two lines of machinery. But `SEQ_FOR_EACH` is built on Boost.PP's `FOR`
state machine: every element pays a recursion-depth probe, a state-tuple
re-parse, and head/tail splitting — measured at roughly **25× more
preprocessing work** than the chain form for typical seqs.

**Chains on** (the default): the same loop *additionally* emits a
*consumption chain* — one macro per possible seq length, each expanding the
body for one element and then naming its successor, which eats the next
`(elem)` by juxtaposition:

```c
#define UNCURSED_PP_DECL_CH1_1(e) UNCURSED_PP_DECL_AP1 e
#define UNCURSED_PP_DECL_CH1_2(e) UNCURSED_PP_DECL_AP1 e UNCURSED_PP_DECL_CH1_1
/* ... up to CH1_16 ... */
#define UNCURSED_PP_DECL_SMALL1(seq) BOOST_PP_CAT(UNCURSED_PP_DECL_CH1_, BOOST_PP_SEQ_SIZE(seq)) seq
#define UNCURSED_PP_DECL_BIG1(seq) BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_DECL_EACH1, ~, seq)
#define UNCURSED_PP_DECL_PICK1(n) BOOST_PP_IIF(BOOST_PP_CAT(UNCURSED_PP_LE16_, n), UNCURSED_PP_DECL_SMALL1, UNCURSED_PP_DECL_BIG1)
#define DECL(fields) UNCURSED_PP_DECL_PICK1(BOOST_PP_SEQ_SIZE(fields))(fields)
```

~2 macro expansions per element instead of `FOR`'s state machine. The
special case of an *identity comma join* (`@join xs with ", ": {{x}}@end`)
is cheaper still: it compiles to a bare `BOOST_PP_SEQ_ENUM(xs)` with no
helpers at all (~50–140×).

## How dispatch works

- `PICK` looks the seq's size up in a **0/1 size-class table**
  (`UNCURSED_PP_LE16_<n>`, one `CAT` + `IIF` per call). The default table
  lives in the shared runtime header; a non-default `loop_chain_limit`
  emits a local `LE<K>` table in the generated header.
- Sizes **≤ K** take the chain (`SMALL`); larger seqs take the unchanged
  `SEQ_FOR_EACH` form (`BIG`) — measured ~0.5% overhead versus chains-off,
  token-identical output. There is **no call-site limit**: a 200-element
  seq just uses the fallback.
- At `loop_chain_limit 256` no seq can exceed the chain
  (`BOOST_PP_LIMIT_SEQ` caps seqs at 256), so `PICK`, `BIG`, the size
  table, and the `seq/for_each.hpp` include are all omitted — the dispatch
  is a single size-`CAT`.

## Configuration

| Pragma | Effect |
|---|---|
| `@pragma loop_chain off` | `SEQ_FOR_EACH` only — smallest headers, slowest preprocessing |
| *(default)* `loop_chain on`, `loop_chain_limit 16` | chains for seqs ≤ 16, fallback above; +~17 lines per loop |
| `@pragma loop_chain_limit K` | tune the boundary; K > 16 grows the chain, K < 16 shrinks it |
| `@pragma loop_chain_limit 256` | full range: no fallback, no size table, no `for_each.hpp` include; 256 chain lines per loop |

## Choosing

- **Header included in many TUs, macros invoked many times** (X-macro
  tables, reflection systems): keep chains on. The chain lines are paid
  once per TU; the ~25× savings is paid per invocation.
- **Header mostly *defined*, rarely invoked**, or you care about generated
  file size (code review, vendoring): `loop_chain off` — this is what the
  README's front-page example does for readability.
- **Huge seqs everywhere and no size concerns**: `loop_chain_limit 256`.

## What never chains

These loops always use the `SEQ_FOR_EACH`/`REPEAT` forms regardless of the
pragma (correctness-driven exclusions):

- **Loops referencing outer parameters** (e.g. `offsetof({{sname}}, ...)`
  inside the loop): chain members are top-level defines and cannot see
  outer macro parameters, so the value rides `FOR_EACH`'s data slot instead.
- **Nested loops** (any loop whose body contains another loop) — inner
  levels iterate via `BOOST_PP_REPEAT`.
- **Identity comma joins** — they get the even-cheaper `SEQ_ENUM` form.

## Sharp edges

- Chain families **merge across loops with identical bodies** (the collapse
  pass merges them as whole families under a shared `HC<n>_` prefix). This
  is deliberate and safe; if you diff generated headers you may see one
  chain serving several macros.
- The chain/fallback boundary is *behavioral parity*, verified token-exact
  in the test suite at K and K+1 — but if you tune `loop_chain_limit`,
  remember a non-default K puts a 256-line `LE<K>` table in *each* generated
  header that uses chains (the default K=16 table is shared via the runtime
  header instead).
- Chain dispatch computes `BOOST_PP_SEQ_SIZE` twice per call (once for the
  pick, once inside `SMALL`). Measured below our optimization threshold
  (1.09–1.25×) — a known, accepted cost.

For the measurement history behind these numbers (and the even faster
variants we chose *not* to ship to keep headers small), see
[optimization.md](optimization.md).
