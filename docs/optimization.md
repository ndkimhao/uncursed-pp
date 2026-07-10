# Codegen optimization: methodology & history

How uncursed-pp's generated Boost.PP code got ~140x cheaper to preprocess, how we
measured it, which ideas were rejected and why. Read this before touching the
emitter's codegen — the refuted section is as binding as the applied one.

## The meta-principle

Boost.PP's primitives pay a genericity tax at C-preprocessing time:
`BOOST_PP_OVERLOAD` scans 65 argument slots, `SEQ_FOLD_LEFT` runs an 8-probe
`AUTO_REC` recursion-depth search per call, `TUPLE_REPLACE`/`SUB`/`LESS` hide
full `BOOST_PP_WHILE` loops, `TUPLE_ELEM` re-dispatches per read. **uncursed-pp
is a generator: everything it knows at generation time — arity, slot indices,
literal comparison operands, parenthesization invariants, element counts —
can replace a generic runtime search with a direct generated form.** Nearly
every win below is an instance of this.

The second principle: know what dominates. `SEQ_FOR_EACH`'s iteration
machinery outweighs typical loop bodies ~40:1, so optimizations *inside* loop
bodies are capped at small end-to-end factors, while optimizations that remove
whole machinery layers (fold, WHILE, 65-slot scans) multiply.

## Measurement methodology

Wall-clock time is noisy; we measure **deterministic cpp allocation**:

```sh
gcc -E -P -fmem-report file.c -o /dev/null 2>&1 | grep -E '^Total +[0-9]' | head -1
# column 2 = total GC allocation; byte-identical across runs for identical input
```

Rules that made results trustworthy (several were learned the hard way — see
the refuted section):

0. **Cap memory, always — derived from the host, never hardcoded.**
   Benchmark TUs at audit scale allocate gigabytes, and parallel measurement
   fan-out multiplies that: an unguarded 15-way audit nearly OOMed a 15G
   host. Before benchmarking, PROBE the machine and derive the budget:

   ```sh
   avail_kb=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
   jobs=$(( $(nproc) / 4 )); [ "$jobs" -lt 1 ] && jobs=1
   per_job_kb=$(( avail_kb / (jobs + 1) ))   # +1 leaves system headroom
   # each measurement: bash -c "ulimit -v $per_job_kb; gcc -E -P ..."
   # at most $jobs measurements in flight at once
   ```

   If a measurement dies against its ulimit, reduce the benchmark scale
   rather than raising the cap. The test harness applies the same
   philosophy: `conftest.run_cpp` caps every `cc` invocation at an eighth
   of physical RAM (clamped to [256 MiB, 2 GiB]) plus a 60s timeout, so
   pathological codegen fails a test instead of the machine.

1. **Token identity is a precondition.** `diff` of `gcc -E -P` outputs
   (whitespace-canonicalized) between current and proposed codegen must be
   empty over an edge-case corpus: comma-containing values, empty
   values/defaults, single-element seqs, repeated keywords, values that are
   themselves macro calls, use inside another macro's argument list.
2. **Realistic scale, and a scale sweep.** cpp's allocation pools double, so a
   single N can show a phantom 1.4x that vanishes at N±1000. Sweep (e.g.
   N=1000..10000) before believing a marginal number.
3. **≥1.3x at realistic scale or it doesn't ship.** Below that, indirection
   and generated-code size aren't worth it (also a stated user preference).
4. **Verify primitives in `/usr/include/boost/preprocessor/`**, never from
   memory. Several findings came *from* reading headers (e.g. discovering
   `TUPLE_REPLACE` → `ARRAY_REPLACE` → `WHILE`).
5. **End-to-end, not just isolated.** An 8x isolated win can be a 1.05x
   end-to-end win if surrounding machinery dominates (see AP-unpacking and
   the refuted `VARIADIC_TO_SEQ` hoist).
6. **Adversarial verification.** Every audit finding was independently
   re-benchmarked from scratch at different scales by a separate reviewer
   pass whose brief was to refute: rebuild, re-measure, attack semantics,
   check the proposal is implementable from generation-time knowledge.
   4 of 12 findings died there — all would have been regressions or bugs.
7. **The regression safety net is the test suite**: hand-reviewed golden
   headers (byte-exact) plus `#?` invocation specs verified **token-exact**
   through real `gcc -E` for every golden macro.

## Applied optimizations

### Round 0 — design-time choices (v1)

| Choice | Rationale |
|---|---|
| Granular `#include`s derived from primitive usage | monolithic `boost/preprocessor.hpp`: 8.4M alloc / 31ms per TU vs 4.4M / 11ms granular; simple macros now include nothing at all |
| 2-arg `BOOST_PP_TUPLE_ELEM` | modern variadic form; the 3-arg size operand is ignored anyway |
| `IIF` over `IF` | skips a `BOOL` when the condition is already 0/1 |
| Branch bodies as selected-then-invoked helpers | commas in branches stay legal; no `EXPAND`/defer tricks |
| Helper collapse pass | identical helpers dedup into `UNCURSED_PP_<FILESTEM>_H<n>`; loop helpers differing by ONE constant merge via the free `d` slot (never more — indirection budget) |

### Round 1 — first measured audit (2026-07-10, commits `6837f27`, `8ad8ae0`)

**Direct slot replacers for named args (14x real-world, 22x isolated).**
`BOOST_PP_TUPLE_REPLACE` hides a `WHILE` per keyword argument; slot indices
are generation-time constants, so per-slot replacers rebuild the state list
directly. Real `widget.h`, 2000 invocations: 2295M → 163M.

**AP-unpacking (9x isolated, 1.5x loop-heavy end-to-end).** Tuple fields
become direct macro parameters: loop bodies move into an `AP` helper applied
to the element *by juxtaposition* (`AP e` — the cheapest unpack that exists),
free outer variables spread in via a `_D` re-parse; macros with tuple params
spread fields into a `BODY` define. Falls back to `TUPLE_ELEM` for whole-tuple
use, name collisions, `as`-bindings. Emergent bonus: simpler helper bodies
collapse better across macros.

Also in this era: keyword-argument *detection* (paste-probe + fold scan per
keyword) was replaced by **setter self-dispatch** — `CAT(SET_, WIDTH(20))`
invokes the keyword's own setter; no probing, and typo'd keywords became
compile errors instead of silently taking defaults.

### Round 2 — multi-agent adversarial audit (commits `e7715fa`..`ac0bb6c`)

| Fix | Gain (verified independently) | Key insight |
|---|---|---|
| Identity comma joins → `BOOST_PP_SEQ_ENUM` | 49–141x | `SEQ_ENUM` is a size-CAT table dispatch; `SEQ_FOR_EACH_I` is a 5-tuple FOR state machine. Gated on body == element, no free vars |
| Relational `@if` → `BOOL(DEC^k(lhs))` chains | ~27x numeric, 5.6x `len()` | `LESS`/`GREATER` funnel into WHILE-based `SUB`; the RHS is always a literal ≤256, and `DEC` saturates at 0. `<`/`<=` swap IIF branch order; `x<0`/`x>=0` constant-fold |
| Named-arg dispatch → per-arity nested setter chains | 8.5–8.8x | `OVERLOAD` already told us the exact kwarg count, so the fold (`AUTO_REC` probe), `VARIADIC_TO_SEQ`, the state tuple, `UNPACK`, and the PUT spread-triple are all unnecessary: `W_3(n, e1, e2) → BODY_D(n, STEP1(e2, STEP1(e1, defaults)))` over *bare* comma state. Innermost step takes the first kwarg → last-wins preserved |
| Per-macro max-arity size scan | 2.06x | replaces `OVERLOAD`'s 65-slot `VARIADIC_SIZE` per call |
| `named variadic` unwrap by juxtaposition | 2.33x | those values are parenthesized *by construction*; the conditional `REMOVE_PARENS` probe (IS_BEGIN_PARENS + IIF + IDENTITY + TUPLE_ENUM) is wasted. User-facing `remove_parens()` keeps the conditional — its argument may be bare |
| k-ary `concat` paste helper for ≥4 args | 1.53x @ 5 args | 2 expansions total vs 2 per nested `CAT` pair; 3-arg measured 1.06x → kept nested |

Cumulative on the same benchmark TU (`widget.h`, 2000 invocations):
**2295M → 16M (~143x)**.

## Refuted ideas — do not re-attempt without new evidence

| Idea | Why it died |
|---|---|
| Hoist `BOOST_PP_STRINGIZE` behind a binding when used ≥3x | the 1.39x was a **pool-doubling artifact**: N-sweep gave 1.00–1.39 noise (1.026x at N=2000). Lesson: sweep scales |
| Hoist multiply-used `REMOVE_PARENS` values behind a variadic slot | **semantic break**: named-variadic values may contain bare commas (documented); the hoisted form re-splits them. Also ~1x at end-to-end scale |
| Hoist `VARIADIC_TO_SEQ` when referenced 2–3x (incl. REFLECT composition) | 1.01–1.03x: one conversion ≈ 6.5k allocs vs ~260k per consuming `SEQ_FOR_EACH` — the loop dominates 40:1 |
| `SEQ_ENUM(SEQ_TRANSFORM(...))` for non-identity comma joins | genuine 2.1x at small sizes but tapering, and the safety gate (which bodies are transform-safe) wasn't cleanly decidable from the AST |
| Replace `COMMA_IF` | it already *is* `IIF(BOOL(i), COMMA, EMPTY)()` + one indirection; measured 1.005x |
| `EQUAL` → `NOT_EQUAL` + branch swap | `NOT_EQUAL` is table-driven; swap saves 1.03x |
| `SEQ_SIZE` eater-chain probes | ≤1.13x or regresses; `SEQ_SIZE` is a trivial paste chain |
| `UNPACK` via `KW_SPREAD` as a standalone fix | 1.17–1.24x against the then-current fold — below bar; subsumed when the fold was deleted outright |
| Inlining `OVERLOAD`'s body or its alias chain | ~1.2x — the 65-slot scan is the cost, not the expansion hops; and inlining aliases breaks `#param` stringize in defaulted bodies |

### Round 3 — chain iteration (retires most of the "inherent" loop cost)

The `SEQ_ENUM` finding generalized: for loops **without free outer
variables**, cursedpp generates SEQ_ENUM-style consumption chains with the
body inlined — each member emits the body for one element plus the next
member's name, which eats the following `(elem)` by juxtaposition
(~2 expansions/element; separators baked between members, no `COMMA_IF`):

| Measurement (8-elem seqs x 3000 calls) | alloc | gain |
|---|---|---|
| `SEQ_FOR_EACH` + AP (previous best) | 865M | — |
| bare chain (hard cap) | 24M | 36x |
| chain + `DEC^16` guard | 75M | 11.5x (guard cost 3x the chain!) |
| **chain + runtime size-class table** (shipped) | **34M** | **25x** |
| above the cap (fallback, 40 elems) | +0.5% | token-identical |

Design: K=16 chain members per loop (dedupe across loops via collapse), a
256-entry `LE16_<n>` 0/1 table in the shared runtime header making the
small/large pick one `CAT`+`IIF`, `SEQ_FOR_EACH` fallback above K (no new
call-site limits). `@pragma loop_chain off` opts out; `@pragma
loop_chain_limit K` tunes (non-default K emits a local `LE<K>` table).
Free-variable loops are excluded structurally: chain members are top-level
defines and cannot see outer macro parameters — they keep the FOR path.
Prototype postmortem: the first chain sketch had a real mechanism bug (a seq
element's parens become the call parens, so members receive the tuple as one
argument and unpack via AP) — caught by the token-identity requirement.

Shipped-bug postmortem: the initial release let the collapse pass merge chain
members *individually*; the dispatch references the family only as a
CAT-assembled **prefix** (`CAT(<M>_CH1_, size)`), which whole-name renames
never see, so two macros with identical loop bodies expanded to an undefined
identifier on the small-seq path. Escaped because the collapse tests were
pinned to `loop_chain off` (blinding the interaction) and string-matched
without running cc. Fixed by family-unit merging (`HC<n>_` shared prefix,
prefix-level rename reaches the CAT) plus a two-identical-loops golden with
small-seq specs. Lesson: when a name is assembled by paste at C time, every
pass that renames must operate on the assembling prefix, and every
interaction of two features needs at least one through-the-compiler test
with both enabled.

## Explicitly accepted costs

- `SEQ_FOR_EACH` / `REPEAT` iteration machinery for loops that **reference
  outer parameters** (chain members can't see them) and for **nested** loops;
  a trivial-body 200-element loop costs ~22M allocs per call there. For
  free-variable-free loops this cost is retired by Round 3's chains.
- Boost.PP magnitude limits: seqs ≤ 256 elements, comparisons/`len()`
  operands 0–256, kwarg count ≤ 64, unbounded tuples ≤ 64 elements
  (documented call-site rules).
- Unbounded tuples lower through a per-loop `TUPLE_TO_SEQ` (the refuted
  `VARIADIC_TO_SEQ`-hoist finding applies: one conversion is ~2% of the
  consuming loop) plus an `IS_EMPTY` gate per loop/`len` so `()` means
  zero elements — correctness, not subject to the ≥1.3x bar. A direct
  comma-consumption chain that skips the conversion is a possible LATER
  measured experiment.
- Per-arity/per-slot generated defines trade header size for expansion count.
  Definition-time cost doesn't scale with invocation count; invocation cost
  does.

## Benchmark reproduction

Ad-hoc benchmark generators and the audit corpus live outside the repo (they
are throwaway by design); the durable regression net is the golden + spec
suite. To re-measure any pattern: generate two headers (current vs proposed
emitter), build a TU with 1000+ invocations, compare `-fmem-report` totals
across an N-sweep, and require token-identical `gcc -E -P` output first.
