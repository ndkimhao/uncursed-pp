# uncursed-pp — DSL reference & user guide

uncursed-pp compiles a readable template DSL (`.uncursed` files) into C preprocessor
macros built on [Boost.Preprocessor](https://www.boost.org/doc/libs/latest/libs/preprocessor/doc/index.html).
This is the complete language reference. For the design rationale see
[design.md](design.md); for per-feature walkthroughs see [`examples/`](../examples/README.md).

## 1. Mental model

A `.uncursed` macro looks like a web render template: the body **is** the C output
text, `{{expr}}` interpolates values, and `@`-directives add control flow.

The crucial difference from an ordinary template engine: loops and conditionals
are **not** expanded when uncursed-pp runs. They compile into Boost.PP machinery
(`BOOST_PP_SEQ_FOR_EACH`, `BOOST_PP_IIF`, ...) that executes **when the C
compiler preprocesses your code**. Call sites pass real, variable-length data:

```text
@macro DECLARE_FIELDS(fields: seq<tuple<type, name>>)
@for (type, name) in fields
  {{type}} {{name}};
@end
@endmacro
```

```c
#include "fields.h"
DECLARE_FIELDS(((int, x))((float, y)))   /* expands to: int x; float y; */
```

Two clocks, three languages: uncursed-pp (Python) runs at *generation time*;
the emitted `#define`s run at *C preprocessing time*; the expansion is plain C.

## 2. Getting started

```sh
make setup                                # mise install + uv sync
uv run uncursed-pp fields.uncursed -o fields.h # compile one template
make test                                 # pytest incl. real `cc -E` e2e tests
```

`uncursed-pp INPUT [-o OUTPUT] [--emit-runtime]` writes `OUTPUT` (default:
input stem + `.h`). Templates whose generated code needs shared utilities
(keyword-argument spreading, the loop-chain size table) `#include` a small
companion header (default `uncursed_pp_runtime.h`) by name. The companion is
only written when you pass `--emit-runtime`; by default the CLI prints a
stderr note when a header needs one (silence it with `--no-emit-runtime`).
Multiple generated headers in one directory share the single runtime file.

## 3. File structure

A `.uncursed` file contains, in any order at the top level:

- **Comments** — lines whose first non-blank character is `#`. Allowed at top
  level and inside macro bodies (the line is dropped entirely).
- **Pragmas** — `@pragma <key> <value>` lines (see §8). By convention at the top.
- **Macro definitions** — `@macro NAME(params)` ... `@endmacro`.
- **Invocation specs** — `#?` / `#=>` comments used by the test suite (see §12).
  They are ordinary comments to the compiler.

All top-level comments — file banners, section notes between macros,
end-of-file notes, and the `#?`/`#=>` spec lines themselves — are
reproduced verbatim as C comments in the generated header, at their
source positions (a header carries its own usage examples). Comments
sitting directly above a macro join that macro's embedded source block
instead.

A macro definition:

```text
@macro NAME(param, param, ...)
<body: raw C text, directives, interpolations>
@endmacro
```

`@endmacro` sits alone on its own line, and the parameter list may span
multiple lines (a trailing comma is allowed):

```text
@macro MAKE_WIDGET(
    name,
    named WIDTH = 100,
    named HEIGHT = 50,
)
```

Macro names and parameter names are C
identifiers. A file may define any number of macros; later macros may invoke
earlier ones *textually* in their bodies (see the reflection example, §13).

## 4. Parameters and types

```text
@macro M(a, xs: seq<token>, f: tuple<type, name>, rest: variadic)
```

| Type | Declares | C call-site shape |
|---|---|---|
| *(none)* / `token` | a single preprocessor token-sequence | `foo`, `123`, `(wrapped, commas)` |
| `tuple<n1, n2, ...>` | a parenthesized tuple with **named** elements | `(int, x)` |
| `tuple` / `tuple<T...>` | an **unbounded** tuple: variable element count, all of type `T` (bare `tuple` = `tuple<token...>`) | `(a, b, c)`; `()` = zero elements |
| `tuple<n1, .., nk, T...>` | **hybrid**: fixed named head fields, then an unbounded `T` tail | `(x, int, RO, LOGGED)`; `(x, int)` = empty tail |
| `seq<T>` | a Boost.PP seq of `T` | `(a)(b)(c)` or `((int,x))((float,y))` |
| `variadic` / `variadic<T>` | the trailing `...`; the body sees it as `seq<T>` | `a, b, c` or `(int,x), (float,y)` |

Notes:

- `tuple` element names are how you access elements: `{{f.type}}`, `{{f.name}}`.
- **Unbounded tuples** (`tuple<T...>` — the ellipsis is what distinguishes
  them from a name list, so `tuple<token>` is still a 1-tuple whose element
  is *named* "token") support `len()`, `[i]` indexing, iteration
  (`@for`/`@join`, with unpacking when `T` is `tuple<...>`), and
  `is_empty()`. Loops and `len()` are emptiness-gated, so `()` means zero
  elements. Elements cap at **64** (vs 256 for seqs); named access is a
  compile error — index instead.
- **Hybrid tuples**: named access reads the head; iteration, `len()`,
  `is_empty()` and `[i]` are all TAIL-scoped (they agree with each other —
  a loop never revisits data you address by name). At least the named
  fields must be present at the call site; head + tail ≤ 64 elements.
- Names bound in scope — parameters, `@for` unpack names, tuple element
  names — compile to macro parameters, so they substitute wherever they
  appear in the body, **including literal C text**. Don't reuse a bound name
  as an ordinary C identifier in the same scope.
- A **seq of tuples needs double parens** at the call site: the outer paren is
  the seq element wrapper (and protects the tuple's commas), the inner is the
  tuple itself: `((int, x))((float, y))`.
- `variadic<tuple<...>>` avoids the double parens: the caller writes
  `M((int, x), (float, y))` and `BOOST_PP_VARIADIC_TO_SEQ` builds the seq form
  internally. A variadic parameter must be **last**, and excludes tail defaults
  and named parameters in the same macro.

### Tail defaults

```text
@macro LOG(msg, level = INFO, out = stderr)
fprintf({{out}}, "[" #{{level}} "] %s\n", {{msg}});
@endmacro
```

`LOG(m)`, `LOG(m, WARN)`, `LOG(m, WARN, stdout)` are all valid; omitted
trailing arguments take their defaults, dispatched **at C compile time** by
argument count (`BOOST_PP_OVERLOAD`). A default may be empty (`qualifiers = `).
Defaults may only trail required parameters, and a default value must not
contain commas or parens.

### Named parameters

```text
@macro MAKE_WIDGET(name, named WIDTH = 100, named HEIGHT = 50, named FLAGS = )
struct widget {{name}} = { {{WIDTH}}, {{HEIGHT}}, {{FLAGS}} };
@endmacro
```

```c
MAKE_WIDGET(w1)                          /* all defaults */
MAKE_WIDGET(w2, HEIGHT(80))              /* any subset  */
MAKE_WIDGET(w3, FLAGS(BOLD), WIDTH(20))  /* any order   */
```

Each named argument is written `KEYWORD(value)` at the call site. Semantics:

- Omitted keywords take their defaults; `FLAGS()` passes an empty value.
- A repeated keyword: the **last occurrence wins** (left-to-right fold).
- A misspelled keyword is a **C compile error** (it fails to dispatch), never a
  silently-applied default.
- Values containing commas must be parenthesized: `WIDTH((a, b))`.
- Keyword names must not be `#define`d macros at the call site.

Rules for combining parameter kinds, enforced by uncursed-pp with clear errors:
one macro may use **at most one** of {tail defaults, named section, variadic};
required parameters come first; defaults/named need at least one required
parameter (C cannot overload on a zero-argument call).

## 5. Interpolation & expressions

`{{expr}}` splices a value into the output. Expressions:

| Expression | Meaning | Compiles to |
|---|---|---|
| `x` | parameter / loop variable / `@let` binding | its value |
| `f.name` | tuple element by declared name | `BOOST_PP_TUPLE_ELEM(i, f)` |
| `xs[0]` | seq element by index | `BOOST_PP_SEQ_ELEM(0, xs)` |
| `xs[0].name` | chains: seq of tuples element access | nested |
| `concat(a, b, ...)` | **explicit** token pasting (≥2 args) | nested `BOOST_PP_CAT` |
| `stringize(x)` | make a C string literal from tokens | `BOOST_PP_STRINGIZE(x)` |
| `remove_parens(x)` | strip ONE paren layer iff present | `BOOST_PP_REMOVE_PARENS(x)` |
| `len(xs)` | element count of a seq/variadic/unbounded tuple | `BOOST_PP_SEQ_SIZE(xs)`; tuples: emptiness-gated `BOOST_PP_TUPLE_SIZE` (so `len(()) == 0`) |
| `is_paren(x)` | 1 if `x` is parenthesized else 0 | `BOOST_PP_IS_BEGIN_PARENS(x)` |
| `is_empty(x)` | 1 if `x` has no tokens (unbounded tuple: no elements) | `BOOST_PP_IS_EMPTY`; conditions, like `is_paren` |

Three rules that surprise newcomers:

- **uncursed-pp never token-pastes implicitly.** `get_{{name}}` produces two
  separate tokens `get_` and `<name>`; to build one identifier write
  `{{concat(get_, name)}}`.
- In `concat(...)` arguments, a name resolves to a variable if one is in
  scope, **otherwise it is a literal token** (like `get_` above).
- The C `#` stringize operator only works on direct macro parameters, so
  `#{{x}}` only works where `x` maps to a real parameter (e.g. tail-default
  bodies). For computed tokens — tuple elements, loop variables — use
  `{{stringize(x)}}`.

`remove_parens` is the comma-protection idiom: callers wrap comma-containing
values in parens, the template unwraps: `PAIR(((pair<int,int>), b))`.

## 6. Directives

### `@for` — loop over a seq

```text
@for (type, name) in fields    # unpack tuple elements (arity must match)
  {{type}} {{name}};
@end

@for x in xs                   # bind each element to x
  f({{x}});
@end
```

If the element type is a tuple and you don't unpack, the tuple's declared
element names are bound implicitly inside the loop.

### `@join` — loop with a separator between items

Block form and inline form (inline is handy inside argument lists):

```text
@join xs as x with " || "
({{x}})
@end

void {{name}}(@join args with ", ": {{type}} {{argname}}@end);
```

- `as x` binds the element; for tuple elements the field names are also
  implicitly available (as with `@for`).
- The separator is any string; `", "` compiles to the cheap
  `BOOST_PP_COMMA_IF`, other separators get a tiny helper macro.

### `@if` / `@else` — compile-time branching

```text
@if len(args) == 1
  explicit_single_arg_init({{name}})
@else
  {{concat(name, _init)}}(@join args with ", ": {{argname}}@end)
@end
```

Inline form: `@if is_paren(x) {{remove_parens(x)}} @else {{x}} @end`.

Conditions are either `is_paren(expr)` or a comparison `lhs OP integer` with
`OP` ∈ `== != < > <= >=` and `lhs` any expression (typically `len(xs)` or a
token parameter). Integer magnitudes are limited to **0–256** (Boost.PP
arithmetic range). Negate `is_paren` by comparing: `@if is_paren(x) == 0`.
`@else` is optional. Branch bodies may contain commas freely — they compile to
separate helper macros selected by `BOOST_PP_IIF`, parameterized by exactly the
variables each branch uses.

### `@let` — generation-time bindings

```text
@let getter := concat(get_, field.name)
{{field.type}} {{getter}}(const struct self *s) { ... }
```

`@let name := value` binds a name usable as `{{name}}` from that point to the
end of the enclosing block (macro body, loop body, or branch). The value is any
expression from §5 — or a single **inline `@join`/`@if`**, whose rendered form
is then reused at every use site (the loop helper is generated once):

```text
@let joined := @join args with ", ": {{argname}}@end
{{fn}}({{joined}}, {{joined}})
```

### Directive cheat-sheet

| Directive | Forms |
|---|---|
| `@for <target> in <seq>` ... `@end` | line form only; target = `(a, b)` or `x` |
| `@join <seq> [as x] with "<sep>"` ... `@end` | line form, or inline `@join ...: body@end` |
| `@if <cond>` ... [`@else` ...] `@end` | line form, or inline `@if c then @else else @end` |
| `@let <name> := <expr or inline @join/@if>` | line form only |
| `@pragma <key> <value>` | top level only |

Inline directives nest (an inline `@if` inside an inline `@join` works); each
inline directive closes with its own `@end` on the same line.

**Nesting limit — 4 loop levels:** the outer loop compiles to
`SEQ_FOR_EACH`; inner loops ride `BOOST_PP_REPEAT`'s three auto-detected
dimensions with `SEQ_ELEM` indexing. Five-deep is a compile error. A
generated macro must still not be invoked from inside another generated
macro's loop body (uncursed-pp cannot see call sites to route reentrancy).
Sequential calls — one generated macro invoking others *outside* any
loop — are fine.

## 7. Loop bodies and outer variables

Loop bodies may reference anything in scope — outer parameters included:

```text
@macro TABLE(sname, fields: seq<tuple<type, name, fmt>>)
@for (type, name, fmt) in fields
  { {{stringize(name)}}, offsetof({{sname}}, {{name}}) },
@end
@endmacro
```

uncursed-pp threads free outer variables (here `sname`) through `FOR_EACH`'s data
slot automatically: one free variable travels as `d` itself, several as a tuple
in `d`. You never manage this; it is mentioned because it is visible in the
generated code.

## 8. Configuration: pragmas

All configuration lives in the template as top-of-file `@pragma` lines —
the CLI takes only the input path and `-o`, so a header regenerates
identically from the source file alone:

| `@pragma` | Default | Meaning |
|---|---|---|
| `pp_prefix P` | `BOOST_PP_` | prefix of the preprocessor library's macros |
| `pp_include "H"` | *(granular)* | single header to include instead of granular ones |
| `pp_include_dir D` | `boost/preprocessor` | root of granular usage-derived includes |
| `helper_prefix P` | `UNCURSED_PP_` | prefix of generated helper macros |
| `runtime_name "F"` | `<helper_prefix>_runtime.h` | filename the shared runtime header is written to |
| `runtime_include I` | `"<runtime_name>"` | the `#include` text generated headers use for the runtime — a path and/or `<...>` form; where the file lives is your include-path contract |
| `loop_chain on\|off` | `on` | consumption-chain iteration for loops without free outer variables (~25x cheaper preprocessing) |
| `loop_chain_limit K` | `16` | chain length: seqs up to K elements take the chain, longer ones the `SEQ_FOR_EACH` fallback (~0.5% overhead). Non-default K emits a local size table; `256` covers every possible seq, dropping the fallback, the size pick, and the `for_each.hpp` include entirely |
| `include "H"` or `include <H>` | — | extra `#include`s appended in order (repeatable) |

Vendored-boost recipes:

```text
@pragma pp_include_dir boost_foo/preprocessor   # same layout, different root
```

```text
@pragma pp_prefix  MYLIB_PP_                    # renamed macros: needs an
@pragma pp_include "mylib/preprocessor.hpp"     # explicit include too
```

A custom `pp_prefix` requires `pp_include` **or** a custom `pp_include_dir`
(the default granular paths only make sense for real Boost).

## 9. Anatomy of the generated code

For each macro, uncursed-pp emits the public `#define` plus namespaced helpers:

- `UNCURSED_PP_<MACRO>_EACHn` — loop bodies (`@for`/`@join`)
- `UNCURSED_PP_<MACRO>_APn` — tuple-element unpackers applied by juxtaposition
  (`AP e`): fields are direct parameters, avoiding a `TUPLE_ELEM` dispatch
  per use
- `UNCURSED_PP_<MACRO>_BODY1` — macros with tuple params spread the fields in
- `UNCURSED_PP_<MACRO>_SEPn` — non-comma join separators
- `UNCURSED_PP_<MACRO>_THENn` / `_ELSEn` — `@if` branches
- `UNCURSED_PP_<MACRO>_SET_<KW>`, `_STEP`, `_PUT_<slot>`, `_BODY`, `_UNPACK`, `_KW`, `_<n>` — named args (slot updates are direct generated replacers; no `TUPLE_REPLACE`/`WHILE`)
- `UNCURSED_PP_<MACRO>_<n>` — tail-default arity chain

Two whole-file passes keep output small and deterministic:

- **Helper collapse**: identical helpers merge into shared
  `UNCURSED_PP_<FILESTEM>_H<n>` macros, numbered in first-use order and
  namespaced by file stem so two generated headers never collide; loop
  helpers differing by exactly one constant token merge with the constant
  passed through the `d` slot. Anything needing more machinery stays
  unmerged on purpose.
- **Granular includes**: only the `boost/preprocessor/*.hpp` headers for
  primitives actually used are included (sorted), unless `pp_include` overrides.

Headers start with `#pragma once` and a provenance comment. Generated files
never edit by hand — recompile the template.

## 10. Call-site rules (C is still C)

1. **Seqs of tuples need double parens**: `F(((int, x))((float, y)))`.
2. **Bare commas break argument counting** — wrap in parens, unwrap with
   `remove_parens()` in the template.
3. **Seq and variadic arguments must be non-empty** (Boost.PP seqs can't be
   empty). `F()` is not a valid call to a variadic macro in v1.
3b. **Seqs cap at 256 elements** (`BOOST_PP_LIMIT_SEQ`); a longer call site
   fails with a cryptic `BOOST_PP_SEQ_SIZE_...` error from the compiler.
4. **Named-arg keywords must not be `#define`d** at the call site.
5. **Comparison magnitudes are 0–256** (`len()` counts included).
6. Arguments are expanded by the preprocessor before dispatch — passing a
   macro that expands to a comma-containing value has the same comma rules.

## 11. Errors

uncursed-pp reports all errors as `file:line[:col]: message` — parse errors
(unknown directive, missing `@endmacro`, bad signature), semantic errors (undefined
variable, iterating a non-seq, unpack arity mismatch, unknown tuple element,
index out of range, parameter-kind mixing), and configuration errors (unknown
pragma, custom prefix without include). The generated header is only written
if compilation succeeds.

## 12. Testing templates: invocation specs

Any template's specs can be verified outside pytest with the standalone
checker — the same canonicalizer and runner the suite uses:

```sh
uv run uncursed-pp-check fields.uncursed --cc clang --work-dir /tmp/dbg -- -I .boost-pp/include
```

Exit codes: 0 all specs pass, 1 a spec failed, 2 setup problems (no
specs, unknown compiler, template errors). `--work-dir` keeps the
generated header and one numbered `.c` snippet per spec for inspection.

Golden templates are self-testing. Append spec comments — the invocation and
its expectations always sit on separate lines:

```text
#?  MAKE_WIDGET(w2, HEIGHT(80))
#=>     struct widget w2 = { 100, 80, };

#?  REFLECT(Point, (int, x, "%d"), (float, y, "%f"))
#=>     typedef struct { int x; float y; } Point;
#=>     enum { Point_field_count = 2 };
```

`tests/test_e2e_specs.py` discovers every `#?` case, compiles the template,
invokes the macro from a C snippet, runs `cc -E -P`, and requires the whole
preprocessed output to EQUAL the joined `#=>` lines, token-exactly (string
literal interiors verbatim, whitespace between tokens normalized) — a missing
or extra emitted token fails. The exact marker `<...>` in an expectation
is a wildcard matching any run of tokens (including none); use as many
per expectation as you like — segments stay
ordered and anchored at both ends, so
`#=> typedef struct { int only; } P2; <...>` pins the struct and elides
the rest. Only the whitespace-free form is magic: `< ... >` written with
spaces stays four ordinary tokens. `#?! INVOCATION` cases assert that preprocessing
must fail (for documented failure modes). Every golden template must carry
specs, and every macro it defines must be exercised (meta-tests enforce both).

## 13. Worked example: a reflection system

`tests/golden/compose/reflect.uncursed` shows the pieces composing. One field
list is the single source of truth:

```text
@macro DEFINE_STRUCT(sname, fields: seq<tuple<type, name, fmt>>)
typedef struct {
@for (type, name, fmt) in fields
  {{type}} {{name}};
@end
} {{sname}};
@endmacro

@macro DEFINE_FIELD_TABLE(sname, fields: seq<tuple<type, name, fmt>>)
static const uncursed_field {{concat(sname, _fields)}}[] = {
@for (type, name, fmt) in fields
  { {{stringize(name)}}, {{stringize(type)}}, offsetof({{sname}}, {{name}}) },
@end
};
enum { {{concat(sname, _field_count)}} = {{len(fields)}} };
@endmacro

@macro DEFINE_PRINTER(sname, fields: seq<tuple<type, name, fmt>>)
static void {{concat(print_, sname)}}(const {{sname}} *v) {
@for (type, name, fmt) in fields
  printf("  " {{stringize(name)}} " = " {{fmt}} "\n", v->{{name}});
@end
}
@endmacro

@macro REFLECT(sname, fields: variadic<tuple<type, name, fmt>>)
DEFINE_STRUCT({{sname}}, {{fields}})
DEFINE_FIELD_TABLE({{sname}}, {{fields}})
DEFINE_PRINTER({{sname}}, {{fields}})
@endmacro
```

```c
REFLECT(Point, (int, x, "%d"), (float, y, "%f"))
/* -> typedef struct { int x; float y; } Point;
      static const uncursed_field Point_fields[] = {
        { "x", "int", offsetof(Point, x) }, { "y", "float", offsetof(Point, y) },
      };
      enum { Point_field_count = 2 };
      static void print_Point(const Point *v) { printf(...); ... }        */
```

Techniques on display: `variadic<tuple<...>>` for single-paren call sites;
`REFLECT` fanning out to sibling macros (sequential composition — allowed);
`stringize()` on computed tokens; `concat()` building identifiers; an outer
parameter (`sname`) used inside loops; `len()` in an interpolation.

## 14. v1 limitations

- Flat loops only (no nesting, no looping macro invoked from a loop body).
- Seqs/variadics must be non-empty at call sites.
- Comparisons only against integer literals 0–256; no arbitrary
  token equality.
- Default values must be single comma-free, paren-free token sequences.
- Boost.PP `list`/`array` types are not modeled (seq/tuple cover the cases).

Generated code favors cheap primitives (`IIF` over `IF`, 2-arg `TUPLE_ELEM`,
shallow helper chains, one fold for all named args) without exotic
expansion tricks — see design.md for the full efficiency stance.
