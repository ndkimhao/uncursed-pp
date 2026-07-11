"""Emit Boost.PP C header text from the uncursed-pp AST."""

from __future__ import annotations

from typing import Any, Iterable

import re
from dataclasses import dataclass, field, replace

from .nodes import (
    BodyNode,
    Concat,
    Cond,
    ElemAccess,
    Expr,
    File,
    ForEach,
    Has,
    If,
    Interp,
    IsEmpty,
    IsParen,
    Join,
    Len,
    Let,
    Literal,
    MacroDef,
    Param,
    RemoveParens,
    SeqT,
    Stringize,
    Text,
    ToSeq,
    ToTuple,
    TokenT,
    TupleT,
    Type,
    VariadicT,
    VarRef,
    VarTupleT,
)
from .meta import render_meta
from .parser import C_LITERAL_PATTERN as C_LITERAL_PATTERN
from .parser import UncursedPpError, parse_file


@dataclass
class EmitConfig:
    pp_prefix: str = "BOOST_PP_"
    pp_include: str | None = None  # None = granular includes from usage
    pp_include_dir: str = "boost/preprocessor"  # root of granular includes
    helper_prefix: str = "UNCURSED_PP_"
    runtime_name: str | None = None  # None = derived from helper_prefix
    runtime_include: str | None = None  # include TEXT for the companion
    # (<...> kept verbatim, else quoted); None = quoted runtime_name.
    # runtime_name stays the filename the companion is WRITTEN to.
    extra_includes: tuple[str, ...] = ()  # user includes appended verbatim
    loop_chain: bool = True  # consumption chains for no-free-var loops
    loop_chain_limit: int = 16  # chain length; larger seqs take FOR_EACH
    arg_prefix: str = ""  # prefix for EVERY generated parameter name
    # (user params, tuple fields, harness slots r/d/e/n/...), so body
    # text can't be captured by them at C-expansion time


# Header (under boost/preprocessor/) providing each primitive we may emit.
_PP_HEADERS = {
    "CAT": "cat.hpp",
    "SEQ_FOR_EACH": "seq/for_each.hpp",
    "REPEAT": "repetition/repeat.hpp",
    "SEQ_FOR_EACH_I": "seq/for_each_i.hpp",
    "SEQ_ELEM": "seq/elem.hpp",
    "SEQ_SIZE": "seq/size.hpp",
    "SEQ_ENUM": "seq/enum.hpp",
    "DEC": "arithmetic/dec.hpp",
    "BOOL": "logical/bool.hpp",
    "SEQ_FOLD_LEFT": "seq/fold_left.hpp",
    "TUPLE_ELEM": "tuple/elem.hpp",
    "STRINGIZE": "stringize.hpp",
    "COMMA_IF": "punctuation/comma_if.hpp",
    "COMMA": "punctuation/comma.hpp",
    "REMOVE_PARENS": "punctuation/remove_parens.hpp",
    "IS_BEGIN_PARENS": "punctuation/is_begin_parens.hpp",
    "IS_EMPTY": "facilities/is_empty_variadic.hpp",
    "TUPLE_TO_SEQ": "tuple/to_seq.hpp",
    "SEQ_TO_TUPLE": "seq/to_tuple.hpp",
    "TUPLE_SIZE": "tuple/size.hpp",
    "IF": "control/if.hpp",
    "IIF": "control/iif.hpp",
    "EMPTY": "facilities/empty.hpp",
    "OVERLOAD": "facilities/overload.hpp",
    "EQUAL": "comparison/equal.hpp",
    "NOT_EQUAL": "comparison/not_equal.hpp",
    "LESS": "comparison/less.hpp",
    "GREATER": "comparison/greater.hpp",
    "LESS_EQUAL": "comparison/less_equal.hpp",
    "GREATER_EQUAL": "comparison/greater_equal.hpp",
    "VARIADIC_TO_SEQ": "variadic/to_seq.hpp",
}

_OP_TO_PP = {
    "==": "EQUAL",
    "!=": "NOT_EQUAL",
    "<": "LESS",
    ">": "GREATER",
    "<=": "LESS_EQUAL",
    ">=": "GREATER_EQUAL",
}


@dataclass
class _Helper:
    name: str
    params: str
    body: str
    # name of the spare SEQ_FOR_EACH data-slot parameter when this helper
    # owns one (loop EACH helpers called with `~` data); the collapse
    # pass may parameterize through it
    data_param: str | None = None


@dataclass
class _MacroOut:
    helpers: list[_Helper] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Binding:
    c_expr: str
    type: Type | None  # None/TokenT = plain token, arity/shape unknown
    fragile: bool = False  # rendered @join/@if call: expands to comma-bearing
    # text, so it cannot travel as a helper argument or data-slot value
    spread: bool = False  # tuple param whose fields are direct BODY params
    wrapped: str | None = None  # parenthesized transport form of a
    # comma-capable value ('named variadic'): ride slots/args as this,
    # unwrap (KW_SPREAD juxtaposition) only at the point of use


_Env = dict[str, _Binding]


class _MacroEmitter:
    """Emits one macro: a main #define plus any generated helper macros."""

    def __init__(
        self,
        macro: MacroDef,
        config: EmitConfig,
        filename: str,
        used: set[str],
        file_state: dict[str, Any],
    ):
        self.macro = macro
        self.config = config
        self.filename = filename
        self.used = used
        self.file_state = file_state
        self.out = _MacroOut()
        self._helper_counts: dict[str, int] = {}
        self._big_name = ""
        self._loop_depth = 0
        self._gated_nodes: set[int] = set()
        self._norm_helpers: dict[tuple[object, ...], str] = {}
        self._has_probes: dict[tuple[object, ...], str] = {}
        self._tuple_err_emitted = False

    def pp(self, name: str) -> str:
        self.used.add(name)
        return f"{self.config.pp_prefix}{name}"

    def arg(self, name: str) -> str:
        """A generated parameter name. @pragma arg_prefix namespaces every
        one of these (user params AND harness slots), so raw body text can
        never be captured by a generated parameter at C-expansion time."""
        return f"{self.config.arg_prefix}{name}"

    def emit(self) -> _MacroOut:
        pos = [p for p in self.macro.params if not p.named and p.default is None]
        defaulted = [p for p in self.macro.params if not p.named and p.default is not None]
        named = [p for p in self.macro.params if p.named]
        variadic = [p for p in self.macro.params if isinstance(p.type, VariadicT)]
        self._validate_params(pos, defaulted, named, variadic)

        env = {
            p.name: _Binding(self._tuple_value(self.arg(p.name), p.type), p.type)
            for p in self.macro.params
        }
        for p in variadic:
            # the body sees the variadic tail as a seq of the element type
            assert isinstance(p.type, VariadicT)
            env[p.name] = _Binding(
                f"{self.pp('VARIADIC_TO_SEQ')}(__VA_ARGS__)", SeqT(p.type.elem)
            )
        for p in named:
            if p.variadic_value:
                # value is parenthesized by construction, so unwrap by
                # juxtaposition - the conditional REMOVE_PARENS probe is
                # wasted work here (audit: 2.33x)
                self.file_state["kw_utils"] = True
                env[p.name] = _Binding(
                    f"{self.config.helper_prefix}KW_SPREAD {self.arg(p.name)}",
                    TokenT(),
                    wrapped=self.arg(p.name),
                )
        spread_params: set[str] = set()
        if not defaulted and not named:
            spread_params = self._spreadable_tuple_params()
            for name in spread_params:
                env[name] = _Binding(self.arg(name), env[name].type, spread=True)
        body = self._render_block(self.macro.body, env)

        if defaulted:
            self._emit_overload_chain(pos, defaulted, body)
        elif named:
            self._emit_named(pos, named, body)
        elif spread_params:
            self._emit_spread_body(spread_params, body)
        else:
            heads = ["..." if isinstance(p.type, VariadicT) else self.arg(p.name) for p in self.macro.params]
            self.out.defines.append(_format_define(f"{self.macro.name}({', '.join(heads)})", body))
        return self.out

    def _spreadable_tuple_params(self) -> set[str]:
        """Tuple params whose fields can become direct BODY parameters.

        Spreading erases the whole-tuple name inside BODY1, but block
        helpers (@if branches, loop bodies) transport values by name and
        the variadic head mapping bypasses BODY1's fixed parameter list -
        so any block construct or variadic param disables spreading.
        """
        if any(isinstance(p.type, VariadicT) for p in self.macro.params):
            return set()
        if _has_blocks(self.macro.body):
            return set()
        taken = {p.name for p in self.macro.params}
        chosen: set[str] = set()
        for p in self.macro.params:
            if not isinstance(p.type, TupleT):
                continue
            fields = p.type.names
            if any(f in taken for f in fields) or len(set(fields)) != len(fields):
                continue
            if _uses_whole(self.macro.body, p.name):
                continue
            taken |= set(fields)
            chosen.add(p.name)
        return chosen

    def _emit_spread_body(self, spread_params: set[str], body: str) -> None:
        """Tuple params spread into a BODY define: every field is a direct
        macro parameter, replacing per-use TUPLE_ELEM dispatches."""
        self.file_state["kw_utils"] = True
        base = f"{self.config.helper_prefix}{self.macro.name}_"
        spread = f"{self.config.helper_prefix}KW_SPREAD"
        body_params: list[str] = []
        args: list[str] = []
        norm_any = False
        for p in self.macro.params:
            if p.name in spread_params:
                assert isinstance(p.type, TupleT)
                body_params.extend(self.arg(n) for n in p.type.names)
                if p.type.maybe:
                    body_params.append(self.arg(f"{p.name}_sz"))
                if p.type.has_optional:
                    norm_any = True
                args.append(f"{spread} {self.arg(p.name)}")
            else:
                body_params.append(self.arg(p.name))
                args.append(self.arg(p.name))
        self.out.defines.append(
            _format_define(f"{base}BODY1({', '.join(body_params)})", body)
        )
        self.out.defines.append(f"#define {base}BODY1_D(...) {base}BODY1(__VA_ARGS__)\n")
        heads = ", ".join(self.arg(p.name) for p in self.macro.params)
        if not norm_any:
            self.out.defines.append(
                f"#define {self.macro.name}({heads}) {base}BODY1_D({', '.join(args)})\n"
            )
            return
        # optional-field tuples normalize one layer EARLY (BODY1_P): the
        # KW_SPREAD in BODY1_P's body sees them as parameters, which is
        # the only position the scanner unwraps
        self.out.defines.append(
            f"#define {base}BODY1_P({heads}) {base}BODY1_D({', '.join(args)})\n"
        )
        outer = ", ".join(
            self._tuple_value(self.arg(p.name), p.type)
            if p.name in spread_params
            else self.arg(p.name)
            for p in self.macro.params
        )
        self.out.defines.append(
            f"#define {self.macro.name}({heads}) {base}BODY1_P({outer})\n"
        )

    def _validate_params(
        self,
        pos: list[Param],
        defaulted: list[Param],
        named: list[Param],
        variadic: list[Param],
    ) -> None:
        line = self.macro.line
        if self.macro.name.startswith(self.config.helper_prefix):
            raise UncursedPpError(
                f"macro name {self.macro.name!r} is inside the generated "
                f"helper namespace ({self.config.helper_prefix}*): generated "
                "or shared helpers could silently redefine it - rename the "
                "macro or change @pragma helper_prefix",
                self.filename,
                line,
            )
        seen_names: set[str] = set()
        for p in self.macro.params:
            if p.name in seen_names:
                raise UncursedPpError(
                    f"duplicate parameter name ${p.name}", self.filename, line
                )
            seen_names.add(p.name)
        if variadic:
            if len(variadic) > 1:
                raise UncursedPpError("at most one variadic parameter", self.filename, line)
            if not isinstance(self.macro.params[-1].type, VariadicT):
                raise UncursedPpError(
                    "the variadic parameter must be last", self.filename, line
                )
            if defaulted or named:
                raise UncursedPpError(
                    "a variadic parameter excludes tail defaults and named params",
                    self.filename,
                    line,
                )
        if defaulted and named:
            raise UncursedPpError(
                "a macro may use tail defaults OR named params, not both",
                self.filename,
                line,
            )
        seen_special = False
        for p in self.macro.params:
            special = p.named or p.default is not None
            if seen_special and not special:
                raise UncursedPpError(
                    f"required parameter {p.name!r} cannot follow a defaulted/named one",
                    self.filename,
                    line,
                )
            seen_special = seen_special or special
        if (defaulted or named) and not pos:
            raise UncursedPpError(
                "defaults/named params need at least one required parameter "
                "(arity dispatch cannot see a zero-argument call)",
                self.filename,
                line,
            )

    def _emit_arity_dispatch(self, base: str, max_arity: int) -> None:
        """Dispatch on argument count with a max-arity-bounded scan.

        BOOST_PP_OVERLOAD runs a 65-slot VARIADIC_SIZE on every invocation;
        the macro's max arity is known at generation time (audit: ~2x).
        """
        defines = self.out.defines
        countdown = ", ".join(str(n) for n in range(max_arity, 0, -1))
        slots = ", ".join(self.arg(f"e{j}") for j in range(max_arity))
        size, n_ = self.arg("size"), self.arg("n")
        defines.append(f"#define {base}SIZE(...) {base}SIZE_I(__VA_ARGS__, {countdown},)\n")
        defines.append(f"#define {base}SIZE_I({slots}, {size}, ...) {size}\n")
        defines.append(f"#define {base}DISPATCH({n_}) {base}DISPATCH_I({n_})\n")
        defines.append(f"#define {base}DISPATCH_I({n_}) {base} ## {n_}\n")
        defines.append(
            f"#define {self.macro.name}(...) "
            f"{base}DISPATCH({base}SIZE(__VA_ARGS__))(__VA_ARGS__)\n"
        )

    def _emit_overload_chain(
        self, pos: list[Param], defaulted: list[Param], body: str
    ) -> None:
        all_params = pos + defaulted
        names = [self.arg(p.name) for p in all_params]
        base = f"{self.config.helper_prefix}{self.macro.name}_"
        total, required = len(all_params), len(pos)
        for k in range(required, total):
            head = f"{base}{k}({', '.join(names[:k])})"
            args = names[:k] + [p.default or "" for p in all_params[k:]]
            self.out.defines.append(f"#define {head} {base}{total}({', '.join(args)})\n")
        self.out.defines.append(_format_define(f"{base}{total}({', '.join(names)})", body))
        self._emit_arity_dispatch(base, total)

    def _emit_named(self, pos: list[Param], named: list[Param], body: str) -> None:
        base = f"{self.config.helper_prefix}{self.macro.name}_"
        pos_names = [self.arg(p.name) for p in pos]
        all_names = pos_names + [self.arg(p.name) for p in named]

        # a required keyword ('required named', default None) initializes
        # its slot with a poison identifier: if never set, it lands in the
        # output and fails C compilation naming the keyword
        def initial(p: Param) -> str:
            value = (
                f"{base}MISSING_REQUIRED_KEYWORD_{p.name}"
                if p.default is None
                else p.default
            )
            return f"({value})" if p.variadic_value else value

        defaults = ", ".join(initial(p) for p in named)
        defines = self.out.defines

        # Each keyword argument dispatches itself: CAT(SET_, WIDTH(20))
        # -> SET_WIDTH(20) -> "0, 20" (slot index, value). 'named variadic'
        # setters capture bare commas and re-wrap so the value stays one
        # macro argument.
        for slot, p in enumerate(named):
            if p.variadic_value:
                defines.append(f"#define {base}SET_{p.name}(...) {slot}, (__VA_ARGS__)\n")
            else:
                defines.append(f"#define {base}SET_{p.name}({self.arg('v')}) {slot}, {self.arg('v')}\n")

        # OVERLOAD already dispatches on the exact keyword count, so each
        # arity nests a one-step setter over BARE comma-separated state -
        # no fold, no seq conversion, no state tuple (audit: 8.5x). STEP_I's
        # ## pastes the literal slot digit the setter emitted; PUT_<slot>
        # rebuilds the state list with that slot replaced. BODY is reached
        # through a variadic redirect because commas produced by expansion
        # never re-split arguments.
        e_, i_, v_ = self.arg("e"), self.arg("i"), self.arg("v")
        defines.append(
            f"#define {base}STEP1({e_}, ...) "
            f"{base}STEP_D({self.pp('CAT')}({base}SET_, {e_}), __VA_ARGS__)\n"
        )
        defines.append(f"#define {base}STEP_D(...) {base}STEP_I(__VA_ARGS__)\n")
        defines.append(
            f"#define {base}STEP_I({i_}, {v_}, ...) {base}PUT_ ## {i_}({v_}, __VA_ARGS__)\n"
        )
        arity = len(named)
        # per-arity heads (and the guard slot below) mix user positional
        # names with generated slots: pick a stem no user param collides
        # with (a positional legitimately named $e1 must keep working)
        slot_stem = "e"
        while any(re.fullmatch(rf"{slot_stem}\d+", p.name) for p in self.macro.params):
            slot_stem += "_"
        # a single keyword slot cannot surface a misspelled keyword: the
        # garbage state is still ONE argument, so BODY's arity would not
        # change. A trailing ~ guard slot pads the state - the typo path
        # collapses state+guard into one blob and BODY hard-errors.
        guarded = arity == 1
        state = [self.arg(f"p{j}") for j in range(arity)]
        if guarded:
            state.append(self.arg(f"{slot_stem}0"))
            defaults = f"{defaults}, ~"
        for slot in range(arity):
            replaced = ", ".join(v_ if j == slot else state[j] for j in range(len(state)))
            defines.append(f"#define {base}PUT_{slot}({v_}, {', '.join(state)}) {replaced}\n")
        body_names = all_names + ([state[-1]] if guarded else [])
        defines.append(_format_define(f"{base}BODY({', '.join(body_names)})", body))
        defines.append(f"#define {base}BODY_D(...) {base}BODY(__VA_ARGS__)\n")

        required = len(pos)
        # arities that cannot possibly carry every required keyword route
        # to a deliberately mismatched function-like macro: calling a
        # 2-parameter macro with one argument is a hard cpp error whose
        # message names the macro (no per-call cost - it is just which
        # define the arity dispatch lands on)
        n_required_kw = sum(1 for p in named if p.default is None)
        if n_required_kw:
            defines.append(
                f"#define {base}ERROR_MISSING_REQUIRED_KEYWORD"
                f"({self.arg('kw')}, {self.arg('missing')})\n"
            )
        for n in range(required, required + n_required_kw):
            defines.append(
                f"#define {base}{n}(...) {base}ERROR_MISSING_REQUIRED_KEYWORD(~)\n"
            )
        if n_required_kw == 0:
            defines.append(
                f"#define {base}{required}({', '.join(pos_names)}) "
                f"{base}BODY({', '.join(pos_names)}, {defaults})\n"
            )
        for k in range(max(1, n_required_kw), arity + 1):
            kw_params = [self.arg(f"{slot_stem}{j}") for j in range(1, k + 1)]
            nest = defaults
            for e in kw_params:  # innermost gets the first kwarg: last wins
                nest = f"{base}STEP1({e}, {nest})"
            defines.append(
                f"#define {base}{required + k}({', '.join(pos_names + kw_params)}) "
                f"{base}BODY_D({', '.join(pos_names)}, {nest})\n"
            )
        self._emit_arity_dispatch(base, required + arity)
        # a keyword repeated past the max arity overflows the bounded
        # size scan: the size slot then holds a keyword CALL (KW(v)),
        # and the dispatch pastes {base}{KW}. Defining those names as
        # error stubs turns the overflow into a hard cpp error - zero
        # per-call cost, k extra defines. (Skipped for a keyword named
        # like generated machinery; the stub must not redefine it.)
        helper_names = {h.name for h in self.out.helpers}
        emitted = "".join(defines)
        err = f"{base}ERROR_TOO_MANY_ARGUMENTS"
        stubs = [
            f"#define {base}{p.name}(...) {err}(~)\n"
            for p in named
            if f"{base}{p.name}" not in helper_names
            and f"#define {base}{p.name}(" not in emitted
        ]
        if stubs:
            defines.append(
                f"#define {err}({self.arg('kw')}, {self.arg('excess')})\n"
            )
            defines.extend(stubs)

    # ── rendering ────────────────────────────────────────────────────

    def _render_block(self, nodes: list[BodyNode], env: _Env) -> str:
        env = dict(env)  # @let bindings stay local to this block
        parts: list[str] = []
        for node in nodes:
            if isinstance(node, Text):
                parts.append(node.value)
            elif isinstance(node, Interp):
                binding = self._resolve(node.expr, env, node.line)
                if isinstance(node.expr, VarRef):
                    self._forbid_maybe(
                        binding.type, "whole-value interpolation", node.line
                    )
                parts.append(binding.c_expr)
            elif isinstance(node, ForEach):
                parts.append(self._render_foreach(node, env))
                parts.append("\n")
            elif isinstance(node, Join):
                parts.append(self._render_join(node, env))
            elif isinstance(node, If):
                parts.append(self._render_if(node, env))
            elif isinstance(node, Let):
                if isinstance(node.expr, Join):
                    env[node.name] = _Binding(
                        self._render_join(node.expr, env), TokenT(), fragile=True
                    )
                elif isinstance(node.expr, If):
                    env[node.name] = _Binding(
                        self._render_if(node.expr, env), TokenT(), fragile=True
                    )
                else:
                    env[node.name] = self._resolve(node.expr, env, node.line)
            else:  # pragma: no cover - future node kinds
                raise NotImplementedError(f"cannot emit {node!r}")
        return _join_segments(parts)

    def _resolve(self, expr: Expr, env: _Env, line: int, allow_literal: bool = False) -> _Binding:
        if isinstance(expr, Literal):
            if allow_literal:
                return _Binding(expr.name, TokenT())
            raise UncursedPpError(
                f"bare name {expr.name!r} is a literal token (allowed in "
                f"concat() arguments); write ${expr.name} for the variable",
                self.filename,
                line,
            )
        if isinstance(expr, VarRef):
            if expr.name in env:
                return env[expr.name]
            raise UncursedPpError(f"undefined variable: ${expr.name}", self.filename, line)
        if isinstance(expr, ElemAccess):
            return self._resolve_access(expr, env, line)
        if isinstance(expr, Concat):
            arg_bindings = [self._resolve(a, env, line, allow_literal=True) for a in expr.args]
            for b in arg_bindings:
                self._forbid_maybe(b.type, "concat()", line)
            rendered = [b.c_expr for b in arg_bindings]
            if len(rendered) >= 4:
                # one k-ary paste (2 expansions total) beats the nested CAT
                # chain (2 per pair); below 4 args nesting measured equal
                helper = self._kary_cat(len(rendered))
                return _Binding(f"{helper}({', '.join(rendered)})", TokenT())
            out = rendered[-1]
            for part in reversed(rendered[:-1]):
                out = f"{self.pp('CAT')}({part}, {out})"
            return _Binding(out, TokenT())
        if isinstance(expr, RemoveParens):
            inner = self._resolve(expr.arg, env, line)
            return _Binding(f"{self.pp('REMOVE_PARENS')}({inner.c_expr})", TokenT())
        if isinstance(expr, Stringize):
            inner = self._resolve(expr.arg, env, line)
            return _Binding(f"{self.pp('STRINGIZE')}({inner.c_expr})", TokenT())
        if isinstance(expr, Len):
            inner = self._resolve(expr.arg, env, line)
            if isinstance(inner.type, VarTupleT):
                # raw TUPLE_SIZE coerces () to 1 (an empty tuple IS one
                # empty element to the preprocessor); gate so len(()) == 0.
                # Hybrids measure the TAIL (must agree with iteration).
                view = self._vtuple_view(inner)
                return _Binding(
                    f"{self.pp('IIF')}({self._isnil()}({view}), 0, "
                    f"{self.pp('TUPLE_SIZE')}({view}))",
                    TokenT(),
                )
            if not isinstance(inner.type, SeqT):
                raise UncursedPpError(
                    "len() needs a seq- or tuple-typed value", self.filename, line
                )
            return _Binding(f"{self.pp('SEQ_SIZE')}({inner.c_expr})", TokenT())
        if isinstance(expr, IsParen):
            inner = self._resolve(expr.arg, env, line)
            return _Binding(f"{self.pp('IS_BEGIN_PARENS')}({inner.c_expr})", TokenT())
        if isinstance(expr, IsEmpty):
            inner = self._resolve(expr.arg, env, line)
            if isinstance(inner.type, VarTupleT):
                return _Binding(f"{self._isnil()}({self._vtuple_view(inner)})", TokenT())
            return _Binding(f"{self.pp('IS_EMPTY')}({inner.c_expr})", TokenT())
        if isinstance(expr, Has):
            access = expr.arg
            assert isinstance(access, ElemAccess) and isinstance(access.accessor, str)
            base = self._resolve(access.base, env, line)
            if not (isinstance(base.type, TupleT) and base.type.maybe):
                raise UncursedPpError(
                    "has() needs a '?' tuple field (declared like "
                    "tuple<$a, $b?>)",
                    self.filename,
                    line,
                )
            if access.accessor not in base.type.names:
                raise UncursedPpError(
                    f"tuple has no element {access.accessor!r} "
                    f"(has: {', '.join(base.type.names)})",
                    self.filename,
                    line,
                )
            j = base.type.names.index(access.accessor)
            if j not in base.type.maybe:
                raise UncursedPpError(
                    f"has() needs a '?' field; ${access.accessor} is always "
                    "present",
                    self.filename,
                    line,
                )
            size_slot = f"{self.pp('TUPLE_ELEM')}({len(base.type.names)}, {base.c_expr})"
            return _Binding(
                f"{self.pp('CAT')}({self._has_probe(base.type, j)}, {size_slot})",
                TokenT(),
            )
        if isinstance(expr, ToSeq):
            inner = self._resolve(expr.arg, env, line)
            if isinstance(inner.type, SeqT):
                return inner  # identity
            if isinstance(inner.type, VarTupleT):
                # hybrids convert their TAIL (consistent with len/iteration)
                return _Binding(
                    f"{self.pp('TUPLE_TO_SEQ')}({self._vtuple_view(inner)})",
                    SeqT(inner.type.elem),
                )
            if isinstance(inner.type, TupleT):
                # names are access sugar; the value converts like any tuple
                self._forbid_maybe(inner.type, "to_seq()", line)
                return _Binding(
                    f"{self.pp('TUPLE_TO_SEQ')}({inner.c_expr})", SeqT(TokenT())
                )
            raise UncursedPpError(
                "to_seq() needs a tuple- or seq-typed value", self.filename, line
            )
        if isinstance(expr, ToTuple):
            inner = self._resolve(expr.arg, env, line)
            if isinstance(inner.type, (VarTupleT, TupleT)):
                self._forbid_maybe(inner.type, "to_tuple()", line)
                return inner  # identity
            if isinstance(inner.type, SeqT):
                return _Binding(
                    f"{self.pp('SEQ_TO_TUPLE')}({inner.c_expr})",
                    VarTupleT(inner.type.elem),
                )
            raise UncursedPpError(
                "to_tuple() needs a seq- or tuple-typed value", self.filename, line
            )
        raise NotImplementedError(f"cannot emit expression {expr!r}")  # pragma: no cover

    def _resolve_access(self, expr: ElemAccess, env: _Env, line: int) -> _Binding:
        base = self._resolve(expr.base, env, line)
        if isinstance(expr.accessor, str):
            if isinstance(base.type, VarTupleT):
                if expr.accessor in base.type.names:
                    idx = base.type.names.index(expr.accessor)
                    return _Binding(
                        f"{self.pp('TUPLE_ELEM')}({idx}, {base.c_expr})", TokenT()
                    )
                if base.type.names:
                    raise UncursedPpError(
                        f"tuple has no named field {expr.accessor!r} "
                        f"(has: {', '.join(base.type.names)}; the tail is "
                        "indexed, not named)",
                        self.filename,
                        line,
                    )
                raise UncursedPpError(
                    f"an unbounded tuple has no named elements; index it: "
                    f"'[0]' instead of '.{expr.accessor}'",
                    self.filename,
                    line,
                )
            if not isinstance(base.type, TupleT):
                raise UncursedPpError(
                    f"named element access '.{expr.accessor}' needs a tuple-typed value",
                    self.filename,
                    line,
                )
            if expr.accessor not in base.type.names:
                raise UncursedPpError(
                    f"tuple has no element {expr.accessor!r} (has: {', '.join(base.type.names)})",
                    self.filename,
                    line,
                )
            if base.spread:
                return _Binding(self.arg(expr.accessor), TokenT())
            idx = base.type.names.index(expr.accessor)
            return _Binding(f"{self.pp('TUPLE_ELEM')}({idx}, {base.c_expr})", TokenT())
        if isinstance(base.type, VarTupleT):
            # tail-relative for hybrids: [i] must agree with len/iteration
            return _Binding(
                self._tuple_value(
                    f"{self.pp('TUPLE_ELEM')}({expr.accessor}, {self._vtuple_view(base)})",
                    base.type.elem,
                ),
                base.type.elem,
            )
        if not isinstance(base.type, SeqT):
            raise UncursedPpError(
                f"indexed access '[{expr.accessor}]' needs a seq- or tuple-typed value",
                self.filename,
                line,
            )
        elem_expr = f"{self.pp('SEQ_ELEM')}({expr.accessor}, {base.c_expr})"
        return _Binding(self._tuple_value(elem_expr, base.type.elem), base.type.elem)

    # ── conditionals ─────────────────────────────────────────────────

    def _guard_fragile(self, names: list[str], env: _Env, line: int) -> None:
        for n in names:
            if env[n].fragile:
                raise UncursedPpError(
                    f"{n!r} holds a @let-bound inline @join/@if; its "
                    "expansion cannot travel into a loop or branch helper — "
                    "write the @join/@if inline at the use site instead",
                    self.filename,
                    line,
                )

    def _render_if(self, node: If, env: _Env) -> str:
        free = self._free_vars(node.then, env, set())
        for name in self._free_vars(node.else_, env, set()):
            if name not in free:
                free.append(name)
        self._guard_fragile(free, env, node.line)
        branch_env = {n: _Binding(self.arg(n), env[n].type) for n in free}
        params = ", ".join(self.arg(n) for n in free)

        then_helper = self._add_helper(
            "THEN", params, _collapse_ws(self._render_block(node.then, branch_env))
        )
        else_helper = self._add_helper(
            "ELSE", params, _collapse_ws(self._render_block(node.else_, branch_env))
        )
        cond, swapped = self._render_cond(node.cond, env, node.line)
        args = ", ".join(env[n].c_expr for n in free)
        if cond == "0":
            return f"{else_helper}({args})"
        if cond == "1":
            return f"{then_helper}({args})"
        first, second = (else_helper, then_helper) if swapped else (then_helper, else_helper)
        return f"{self.pp('IIF')}({cond}, {first}, {second})({args})"

    def _render_cond(self, cond: Cond, env: _Env, line: int) -> tuple[str, bool]:
        """Render a condition to (0/1-valued expression, branches-swapped).

        Relational ops with a literal RHS compile to a saturating
        BOOST_PP_DEC chain + BOOST_PP_BOOL instead of LESS/GREATER etc.,
        whose SUB hides a full BOOST_PP_WHILE (audit: ~27x). BOOL(DEC^k(x))
        is 1 iff x > k, so > / >= read it directly and < / <= swap branches;
        k below zero constant-folds ("0"/"1" sentinel = always else/then).
        """
        if isinstance(cond, (IsParen, IsEmpty, Has)):
            return self._resolve(cond, env, line).c_expr, False
        if not 0 <= cond.value <= 256:
            raise UncursedPpError(
                f"comparison literal {cond.value} is outside Boost.PP's "
                "0-256 magnitude range",
                self.filename,
                line,
            )
        lhs = self._resolve(cond.lhs, env, line).c_expr
        if cond.op in ("==", "!="):
            return f"{self.pp(_OP_TO_PP[cond.op])}({lhs}, {cond.value})", False
        swapped = cond.op in ("<", "<=")
        k = cond.value - 1 if cond.op in ("<", ">=") else cond.value
        if k < 0:  # x < 0 impossible; x >= 0 vacuous (values are 0..256)
            return ("0" if cond.op == "<" else "1"), False
        expr = lhs
        for _ in range(k):
            expr = f"{self.pp('DEC')}({expr})"
        return f"{self.pp('BOOL')}({expr})", swapped

    def _free_vars(self, nodes: list[BodyNode], env: _Env, bound: set[str]) -> list[str]:
        """Names from `env` referenced by `nodes`, in first-use order."""
        out: list[str] = []

        def add(name: str) -> None:
            if name in env and name not in bound and name not in out:
                out.append(name)

        def walk_expr(e: Expr) -> None:
            if isinstance(e, VarRef):
                add(e.name)
            elif isinstance(e, ElemAccess):
                walk_expr(e.base)
            elif isinstance(e, Concat):
                for a in e.args:
                    walk_expr(a)
            elif isinstance(e, (RemoveParens, Stringize, Len, IsParen, IsEmpty, Has, ToSeq, ToTuple)):
                walk_expr(e.arg)

        def loop_names(n: ForEach | Join) -> set[str]:
            names: set[str] = set()
            unpack = n.unpack if isinstance(n, ForEach) else None
            var = n.var
            if unpack:
                names.update(unpack)
            elif var:
                names.add(var)
            binding = env.get(n.iterable)
            if not unpack and binding and isinstance(binding.type, (SeqT, VarTupleT)):
                if isinstance(binding.type.elem, TupleT):
                    names.update(binding.type.elem.names)
            return names

        def walk(nodes: list[BodyNode], bound: set[str]) -> None:
            local_bound = set(bound)
            for n in nodes:
                if isinstance(n, Interp):
                    walk_expr(n.expr)
                elif isinstance(n, Let):
                    if isinstance(n.expr, (Join, If)):
                        walk([n.expr], local_bound)
                    else:
                        walk_expr(n.expr)
                    local_bound.add(n.name)
                elif isinstance(n, (ForEach, Join)):
                    add(n.iterable)
                    walk(n.body, local_bound | loop_names(n))
                elif isinstance(n, If):
                    if isinstance(n.cond, (IsParen, IsEmpty, Has)):
                        walk_expr(n.cond.arg)
                    else:
                        walk_expr(n.cond.lhs)
                    walk(n.then, local_bound)
                    walk(n.else_, local_bound)

        walk(nodes, bound)
        return out

    # ── loops ────────────────────────────────────────────────────────

    def _loop_env(
        self,
        env: _Env,
        unpack: tuple[str, ...] | None,
        var: str | None,
        elem_type: Type,
        node: ForEach | Join,
        elem: str = "e",
    ) -> _Env:
        loop_env = dict(env)
        elem = self._tuple_value(elem, elem_type)
        if unpack is not None:
            if not isinstance(elem_type, TupleT):
                raise UncursedPpError("tuple unpacking needs a seq of tuples", self.filename, node.line)
            if len(unpack) != len(elem_type.names):
                raise UncursedPpError(
                    f"unpack arity {len(unpack)} != tuple arity {len(elem_type.names)}",
                    self.filename,
                    node.line,
                )
            names: tuple[str, ...] = unpack
        elif var is not None:
            loop_env[var] = _Binding(elem, elem_type)
            names = ()
        else:
            names = elem_type.names if isinstance(elem_type, TupleT) else ()
        for idx, name in enumerate(names):
            loop_env[name] = _Binding(f"{self.pp('TUPLE_ELEM')}({idx}, {elem})", TokenT())
        return loop_env

    def _iterable_binding(self, name: str, env: _Env, node: ForEach | Join) -> tuple[str, Type]:
        """Resolve an iterated name to (c_expr, element type). Unbounded
        tuples lower to their seq form HERE, at the point of iteration -
        the name's own binding stays tuple-typed, so whole-value and
        named-head uses inside the loop body still see the tuple."""
        binding = env.get(name)
        if binding is None:
            raise UncursedPpError(f"undefined variable: {name}", self.filename, node.line)
        if isinstance(binding.type, VarTupleT):
            return (
                f"{self.pp('TUPLE_TO_SEQ')}({self._vtuple_view(binding)})",
                binding.type.elem,
            )
        if not isinstance(binding.type, SeqT):
            raise UncursedPpError(f"cannot iterate non-seq {name!r}", self.filename, node.line)
        return binding.c_expr, binding.type.elem

    def _loop_data(
        self, node: ForEach | Join, env: _Env, loop_env: _Env
    ) -> tuple[str, _Env, list[str]]:
        """Free outer variables in a loop body ride FOR_EACH's `d` slot.

        Returns (data argument for the call site, adjusted loop env, and
        the names riding `d` in slot order). One free variable travels as
        `d` itself; several as a tuple in `d`.
        """
        # bound = names the loop itself introduced or rebound (unpack names,
        # `as` var, implicit tuple fields); everything else from the outer
        # env is free and must travel through `d`.
        bound = {name for name, binding in loop_env.items() if env.get(name) != binding}
        free = self._free_vars(node.body, env, bound)
        self._guard_fragile(free, env, node.line)
        if not free:
            return "~", loop_env, []
        loop_env = dict(loop_env)

        def transport(binding: _Binding, slot: str) -> _Binding:
            # comma-capable values ride the slot in their WRAPPED form and
            # unwrap at use; juxtaposition works on computed slot refs too
            # (the unexpanded KW_SPREAD meets the parens after they expand)
            if binding.wrapped is not None:
                return _Binding(
                    f"{self.config.helper_prefix}KW_SPREAD {slot}",
                    binding.type,
                    wrapped=slot,
                )
            return _Binding(slot, binding.type)

        if len(free) == 1:
            b = env[free[0]]
            data = b.wrapped or b.c_expr
            loop_env[free[0]] = transport(b, self.arg("d"))
        else:
            data = "(" + ", ".join(env[n].wrapped or env[n].c_expr for n in free) + ")"
            for idx, name in enumerate(free):
                loop_env[name] = transport(
                    env[name], f"{self.pp('TUPLE_ELEM')}({idx}, {self.arg('d')})"
                )
        return data, loop_env, free

    def _render_inner_loop(
        self,
        node: ForEach | Join,
        env: _Env,
        seq_expr: str,
        elem_type: Type,
        sep: str | None,
    ) -> str:
        """Loops below the outermost level iterate via BOOST_PP_REPEAT.

        SEQ_FOR_EACH cannot re-enter itself, but REPEAT auto-detects its
        repetition dimension (3 available), so inner loops index the seq
        with SEQ_ELEM(n, ...). The seq travels in slot 0 of the data
        tuple, free outer variables in the following slots. AP spreading
        is skipped here: juxtaposition cannot apply a helper to a
        SEQ_ELEM-computed element.
        """
        if self._loop_depth > 3:
            raise UncursedPpError(
                "loops nest at most 4 deep: the outer level uses "
                "SEQ_FOR_EACH and BOOST_PP_REPEAT provides 3 reentrant "
                "dimensions for inner loops",
                self.filename,
                node.line,
            )
        unpack = node.unpack if isinstance(node, ForEach) else None
        # preliminary env only to learn which names this loop introduces
        prelim = self._loop_env(env, unpack, node.var, elem_type, node)
        bound = {n for n, b in prelim.items() if env.get(n) != b}
        free = [n for n in self._free_vars(node.body, env, bound) if n != node.iterable]
        self._guard_fragile(free, env, node.line)

        if free:
            # comma-capable values (wrapped) travel in their wrapped form
            parts = [seq_expr] + [env[n].wrapped or env[n].c_expr for n in free]
            data = "(" + ", ".join(parts) + ")"
            seq_ref = f"{self.pp('TUPLE_ELEM')}(0, {self.arg('d')})"
        else:
            data = seq_expr
            seq_ref = self.arg("d")
        base_env = dict(env)
        for idx, name in enumerate(free):
            slot = f"{self.pp('TUPLE_ELEM')}({idx + 1}, {self.arg('d')})"
            if env[name].wrapped is not None:
                base_env[name] = _Binding(
                    f"{self.config.helper_prefix}KW_SPREAD {slot}",
                    env[name].type,
                    wrapped=slot,
                )
            else:
                base_env[name] = _Binding(slot, env[name].type)
        if node.iterable in env:
            base_env[node.iterable] = _Binding(seq_ref, env[node.iterable].type)
        elem = f"{self.pp('SEQ_ELEM')}({self.arg('n')}, {seq_ref})"
        loop_env = self._loop_env(base_env, unpack, node.var, elem_type, node, elem=elem)

        self._loop_depth += 1
        try:
            body = _collapse_ws(self._render_block(node.body, loop_env))
        finally:
            self._loop_depth -= 1

        if sep is not None:
            if sep == ",":
                body = f"{self.pp('COMMA_IF')}({self.arg('n')}) {body}"
            else:
                sep_helper = self._add_helper("SEP", "", sep)
                body = (
                    f"{self.pp('IF')}({self.arg('n')}, {sep_helper}, "
                    f"{self.pp('EMPTY')})() {body}"
                )
        helper = self._add_helper(
            "EACH", f"{self.arg('z')}, {self.arg('n')}, {self.arg('d')}", body
        )
        return f"{self.pp('REPEAT')}({self.pp('SEQ_SIZE')}({seq_expr}), {helper}, {data})"

    def _loop_body(
        self, node: ForEach | Join, env: _Env, elem_type: Type
    ) -> tuple[str, str]:
        """Render a loop body; returns (each-helper body core, data argument).

        For tuple elements the body goes into an AP helper applied to the
        element by juxtaposition (`AP e`), so every field is a direct macro
        parameter instead of a per-use BOOST_PP_TUPLE_ELEM dispatch. Free
        outer variables ride the `d` slot and are spread into AP params too.
        """
        unpack = node.unpack if isinstance(node, ForEach) else None
        loop_env = self._loop_env(env, unpack, node.var, elem_type, node, elem=self.arg("e"))
        # free = exactly the names _loop_data rebound onto the d slot, in
        # slot order (never re-derived by string-matching c_exprs: a param
        # literally named $d, or a @let over a field of it, must not be
        # mistaken for a rider)
        data, loop_env, free = self._loop_data(node, env, loop_env)

        field_names: tuple[str, ...] = ()
        if node.var is None and isinstance(elem_type, TupleT):
            field_names = unpack or elem_type.names
        ap_params = free + list(field_names)

        use_ap = bool(field_names) and len(set(ap_params)) == len(ap_params)
        render_env = loop_env
        if use_ap:
            render_env = dict(loop_env)
            for name in ap_params:
                if loop_env[name].wrapped is not None:
                    # the AP param receives the still-wrapped value
                    render_env[name] = _Binding(
                        f"{self.config.helper_prefix}KW_SPREAD {self.arg(name)}",
                        loop_env[name].type,
                        wrapped=self.arg(name),
                    )
                else:
                    render_env[name] = _Binding(self.arg(name), loop_env[name].type)
        self._loop_depth += 1
        try:
            body = _collapse_ws(self._render_block(node.body, render_env))
        finally:
            self._loop_depth -= 1
        if not use_ap:
            return body, data

        ap_param_names = [self.arg(n) for n in ap_params]
        norm = isinstance(elem_type, TupleT) and elem_type.has_optional
        if isinstance(elem_type, TupleT) and elem_type.maybe:
            ap_param_names.append(self.arg("fsz"))  # hidden size slot
        ap = self._add_helper("AP", ", ".join(ap_param_names), body)
        # optional-field elems must normalize one layer EARLY: the scanner
        # never revisits a name it already passed, so juxtaposition (and
        # KW_SPREAD) only work on values that arrive as parameters
        t_ = self.arg("t")
        if not free:
            if not norm:
                return f"{ap} {self.arg('e')}", data
            self.out.helpers.append(
                _Helper(name=f"{ap}_N", params=t_, body=f"{ap} {t_}")
            )
            return f"{ap}_N({self._tuple_value(self.arg('e'), elem_type)})", data
        self.file_state["kw_utils"] = True
        spread = f"{self.config.helper_prefix}KW_SPREAD"
        d_part = self.arg("d") if len(free) == 1 else f"{spread} {self.arg('d')}"
        self.out.helpers.append(
            _Helper(name=f"{ap}_D", params="...", body=f"{ap}(__VA_ARGS__)")
        )
        if not norm:
            return f"{ap}_D({d_part}, {spread} {self.arg('e')})", data
        d_ = self.arg("d")
        inner_d = d_ if len(free) == 1 else f"{spread} {d_}"
        self.out.helpers.append(
            _Helper(
                name=f"{ap}_P",
                params=f"{d_}, {t_}",
                body=f"{ap}_D({inner_d}, {spread} {t_})",
            )
        )
        return f"{ap}_P({self.arg('d')}, {self._tuple_value(self.arg('e'), elem_type)})", data

    def _render_chain(self, body: str, sep: str, seq_expr: str, elem_var: str) -> str:
        """Consumption-chain iteration: each member emits the body for one
        element plus the next member's name, which eats the following (elem)
        by juxtaposition - ~2 expansions/element vs FOR's state machine
        (audit: ~25x with the size-class guard). Sizes above the chain
        length fall back to the FOR_EACH path (measured 0.5% overhead)."""
        base = f"{self.config.helper_prefix}{self.macro.name}_"
        k = self.config.loop_chain_limit
        count = self._helper_counts.get("CH", 0) + 1
        self._helper_counts["CH"] = count
        chain = f"{base}CH{count}_"
        self.out.helpers.append(_Helper(name=f"{chain}1", params=elem_var, body=body))
        for n in range(2, k + 1):
            self.out.helpers.append(
                _Helper(name=f"{chain}{n}", params=elem_var, body=f"{body}{sep} {chain}{n - 1}")
            )
        if k >= 256:
            # full-range chain: no seq can exceed it (BOOST_PP_LIMIT_SEQ),
            # so the size pick, the FOR_EACH fallback, and its include are
            # all unnecessary - but the dispatch still routes through a
            # SMALL wrapper: a computed seq (variadic tail, tuple
            # lowering, to_seq value) must pre-expand as a macro ARGUMENT
            # before the chain member can juxtapose-consume it
            self._big_name = ""
            small = f"{base}SMALL{count}"
            seq_ = self.arg("seq")
            self.out.defines.append(
                f"#define {small}({seq_}) "
                f"{self.pp('CAT')}({chain}, {self.pp('SEQ_SIZE')}({seq_})) {seq_}\n"
            )
            return f"{small}({seq_expr})"
        self.file_state["chain_tables"].add(k)
        table = f"{self.config.helper_prefix}LE{k}_"
        small = f"{base}SMALL{count}"
        big = f"{base}BIG{count}"
        pick = f"{base}PICK{count}"
        seq_ = self.arg("seq")
        self.out.defines.append(
            f"#define {small}({seq_}) "
            f"{self.pp('CAT')}({chain}, {self.pp('SEQ_SIZE')}({seq_})) {seq_}\n"
        )
        self._big_name = big  # filled by the caller with the FOR_EACH form
        n_ = self.arg("n")
        self.out.defines.append(
            f"#define {pick}({n_}) "
            f"{self.pp('IIF')}({self.pp('CAT')}({table}, {n_}), {small}, {big})\n"
        )
        return f"{pick}({self.pp('SEQ_SIZE')}({seq_expr}))({seq_expr})"

    def _chain_eligible(self, node: ForEach | Join, data: str) -> bool:
        return (
            self.config.loop_chain
            and data == "~"
            and not _contains_loop(node.body)
        )

    def _tail_of(self, binding: _Binding) -> str:
        """Extract a hybrid tuple's unbounded tail: drop the named head
        fields, re-wrap the rest. The argument expands before `_I t`
        juxtaposes (ISNIL trick), so computed values work; the result is
        a plain unbounded tuple that every VarTupleT path consumes."""
        assert isinstance(binding.type, VarTupleT)
        k = len(binding.type.names)
        name = f"{self.config.helper_prefix}{self.macro.name}_TL{k}"
        if not any(h.name == name for h in self.out.helpers):
            heads = ", ".join(self.arg(f"f{j}") for j in range(k))
            t_ = self.arg("t")
            self.out.helpers.append(_Helper(name=name, params=t_, body=f"{name}_I {t_}"))
            self.out.helpers.append(
                _Helper(name=f"{name}_I", params=f"{heads}, ...", body="(__VA_ARGS__)")
            )
        return f"{name}({binding.c_expr})"

    def _vtuple_view(self, binding: _Binding) -> str:
        """The unbounded part of a VarTupleT value: the value itself, or
        the extracted tail when named head fields precede it."""
        assert isinstance(binding.type, VarTupleT)
        return self._tail_of(binding) if binding.type.names else binding.c_expr

    def _tuple_norm(self, t: TupleT) -> str:
        """Size-dispatched normalizer for optional trailing fields: pads
        a call-site tuple to full width (omitted `= def` fields filled,
        omitted `?` fields empty, plus a trailing size slot when the
        shape has `?` fields). CAT(NF_, TUPLE_SIZE(t)) t consumes the
        argument-pre-expanded tuple by juxtaposition; sizes below the
        required count land on a mismatched error stub."""
        key = (t.names, t.defaults, t.maybe)
        if key in self._norm_helpers:
            return self._norm_helpers[key]
        base = f"{self.config.helper_prefix}{self.macro.name}_"
        name = f"{base}NF{len(self._norm_helpers) + 1}"
        self._norm_helpers[key] = name
        total, req = len(t.names), t.required
        t_, n_ = self.arg("t"), self.arg("n")
        d = self.out.defines
        d.append(f"#define {name}({t_}) {name}_D({self.pp('TUPLE_SIZE')}({t_}), {t_})\n")
        d.append(f"#define {name}_D(...) {name}_I(__VA_ARGS__)\n")
        d.append(f"#define {name}_I({n_}, {t_}) {name}_ ## {n_} {t_}\n")
        if req > 1:
            err = f"{base}ERROR_TOO_FEW_TUPLE_FIELDS"
            if not self._tuple_err_emitted:
                self._tuple_err_emitted = True
                d.append(f"#define {err}({self.arg('need')}, {self.arg('got')})\n")
            for k in range(1, req):
                d.append(f"#define {name}_{k}(...) {err}(~)\n")
        for k in range(req, total + 1):
            heads = [self.arg(f"f{j}") for j in range(k)]
            filled = heads + [t.defaults[j] or "" for j in range(k, total)]
            if t.maybe:
                filled.append(str(k))
            d.append(f"#define {name}_{k}({', '.join(heads)}) ({', '.join(filled)})\n")
        return name

    def _tuple_value(self, expr: str, t: Type | None) -> str:
        """Normalize a raw tuple value at its binding-creation point when
        its type carries optional fields; identity otherwise."""
        if isinstance(t, TupleT) and t.has_optional:
            return f"{self._tuple_norm(t)}({expr})"
        return expr

    def _has_probe(self, t: TupleT, j: int) -> str:
        """0/1 lookup for field-j presence, dispatched by pasting the
        normalized tuple's size slot: HP<i>_<k> is 1 iff k > j."""
        key = (t.names, t.defaults, t.maybe, j)
        if key in self._has_probes:
            return self._has_probes[key]
        base = f"{self.config.helper_prefix}{self.macro.name}_"
        name = f"{base}HP{len(self._has_probes) + 1}_"
        self._has_probes[key] = name
        for k in range(t.required, len(t.names) + 1):
            self.out.defines.append(f"#define {name}{k} {1 if k > j else 0}\n")
        return name

    def _forbid_maybe(self, t: Type | None, what: str, line: int) -> None:
        if isinstance(t, TupleT) and t.maybe:
            raise UncursedPpError(
                f"{what} cannot take a tuple with '?' fields (an absent "
                "field has no whole-value form; access fields or use has())",
                self.filename,
                line,
            )

    def _isnil(self) -> str:
        """0/1 emptiness probe for a parenthesized value. The argument is
        macro-expanded before substitution, then juxtaposition makes its
        own parens IS_EMPTY's call parens — safe for computed values
        (TUPLE_ELEM(...)-shaped) where bare juxtaposition would misfire."""
        name = f"{self.config.helper_prefix}{self.macro.name}_ISNIL"
        if not any(h.name == name for h in self.out.helpers):
            x_ = self.arg("x")
            self.out.helpers.append(
                _Helper(name=name, params=x_, body=f"{self.pp('IS_EMPTY')} {x_}")
            )
        return name

    def _gate_var_tuple(self, node: ForEach | Join, env: _Env) -> str | None:
        """A loop over an unbounded tuple: () must iterate ZERO times, but
        to the preprocessor () is one empty element. Wrap the
        TUPLE_TO_SEQ-lowered loop in an emptiness gate using the
        selected-then-invoked branch pattern (commas in the loop call
        stay legal). Inside the branch helper the iterable is seq-typed,
        so the recursive render takes the normal loop path."""
        binding = env.get(node.iterable)
        if binding is None or not isinstance(binding.type, VarTupleT):
            return None
        if id(node) in self._gated_nodes:
            return None  # already inside this node's gate branch
        free = self._free_vars([node], env, set())
        self._guard_fragile(free, env, node.line)
        # the branch helper's params hold the raw values - including the
        # whole tuple itself; _iterable_binding lowers it to a seq only
        # at the point of iteration, so body uses of the name (whole
        # value, hybrid named head) still see the tuple
        branch_env = {n: _Binding(self.arg(n), env[n].type) for n in free}
        self._gated_nodes.add(id(node))
        try:
            if isinstance(node, ForEach):
                body = self._render_foreach(node, branch_env)
            else:
                body = self._render_join(node, branch_env)
        finally:
            self._gated_nodes.discard(id(node))
        params = ", ".join(self.arg(n) for n in free)
        loop_helper = self._add_helper("LOOP", params, _collapse_ws(body))
        nil_helper = self._add_helper("NIL", params, "")
        args = ", ".join(env[n].c_expr for n in free)
        return (
            f"{self.pp('IIF')}({self._isnil()}({self._vtuple_view(binding)}), "
            f"{nil_helper}, {loop_helper})({args})"
        )

    def _render_foreach(self, loop: ForEach, env: _Env) -> str:
        gated = self._gate_var_tuple(loop, env)
        if gated is not None:
            return gated
        seq_expr, elem_type = self._iterable_binding(loop.iterable, env, loop)
        if self._loop_depth:
            return self._render_inner_loop(loop, env, seq_expr, elem_type, sep=None)
        body, data = self._loop_body(loop, env, elem_type)
        if self.config.loop_chain_limit >= 256 and self._chain_eligible(loop, data):
            return self._render_chain(body, "", seq_expr, self.arg("e"))
        helper = self._add_helper(
            "EACH",
            f"{self.arg('r')}, {self.arg('d')}, {self.arg('e')}",
            body,
            data_param=self.arg("d"),
        )
        each_call = f"{self.pp('SEQ_FOR_EACH')}({helper}, {data}, "
        if self._chain_eligible(loop, data):
            call = self._render_chain(body, "", seq_expr, self.arg("e"))
            self.out.defines.append(
                f"#define {self._big_name}({self.arg('seq')}) {each_call}{self.arg('seq')})\n"
            )
            return call
        return f"{each_call}{seq_expr})"

    def _render_join(self, join: Join, env: _Env) -> str:
        gated = self._gate_var_tuple(join, env)
        if gated is not None:
            return gated
        seq_expr, elem_type = self._iterable_binding(join.iterable, env, join)
        if self._loop_depth:
            return self._render_inner_loop(join, env, seq_expr, elem_type, sep=join.sep.strip())
        body, data = self._loop_body(join, env, elem_type)
        sep = join.sep.strip()
        if sep == "," and body == self.arg("e") and data == "~":
            # identity comma join: SEQ_ENUM is table-driven, ~50-140x cheaper
            # than the FOR-based SEQ_FOR_EACH_I + COMMA_IF machinery
            return f"{self.pp('SEQ_ENUM')}({seq_expr})"
        if self.config.loop_chain_limit >= 256 and self._chain_eligible(join, data):
            chain_sep = "," if sep == "," else f" {sep}"
            return self._render_chain(body, chain_sep, seq_expr, self.arg("e"))
        if sep == ",":
            each_body = f"{self.pp('COMMA_IF')}({self.arg('i')}) {body}"
        else:
            sep_helper = self._add_helper("SEP", "", sep)
            each_body = (
                f"{self.pp('IF')}({self.arg('i')}, {sep_helper}, "
                f"{self.pp('EMPTY')})() {body}"
            )
        helper = self._add_helper(
            "EACH",
            f"{self.arg('r')}, {self.arg('d')}, {self.arg('i')}, {self.arg('e')}",
            each_body,
            data_param=self.arg("d"),
        )
        each_call = f"{self.pp('SEQ_FOR_EACH_I')}({helper}, {data}, "
        if self._chain_eligible(join, data):
            chain_sep = "," if sep == "," else f" {sep}"
            call = self._render_chain(body, chain_sep, seq_expr, self.arg("e"))
            self.out.defines.append(
                f"#define {self._big_name}({self.arg('seq')}) {each_call}{self.arg('seq')})\n"
            )
            return call
        return f"{each_call}{seq_expr})"

    def _kary_cat(self, k: int) -> str:
        name = f"{self.config.helper_prefix}{self.macro.name}_CAT{k}"
        if not any(h.name == name for h in self.out.helpers):
            params = ", ".join(self.arg(f"p{j}") for j in range(k))
            pasted = " ## ".join(self.arg(f"p{j}") for j in range(k))
            self.out.helpers.append(_Helper(name=name, params=params, body=f"{name}_I({params})"))
            self.out.helpers.append(_Helper(name=f"{name}_I", params=params, body=pasted))
        return name

    def _add_helper(
        self, kind: str, params: str, body: str, *, data_param: str | None = None
    ) -> str:
        count = self._helper_counts.get(kind, 0) + 1
        self._helper_counts[kind] = count
        name = f"{self.config.helper_prefix}{self.macro.name}_{kind}{count}"
        self.out.helpers.append(
            _Helper(name=name, params=params, body=body, data_param=data_param)
        )
        return name


def _has_blocks(nodes: list[BodyNode]) -> bool:
    """True if any @if/@for/@join lives in this body (incl. @let-bound)."""
    for n in nodes:
        if isinstance(n, (If, ForEach, Join)):
            return True
        if isinstance(n, Let) and isinstance(n.expr, (Join, If)):
            return True
    return False


def _contains_loop(nodes: list[BodyNode]) -> bool:
    """True if a @for/@join lives in this body (incl. inside @if branches
    and @let-bound joins) - such bodies are ineligible for chain iteration."""
    for n in nodes:
        if isinstance(n, (ForEach, Join)):
            return True
        if isinstance(n, If) and (_contains_loop(n.then) or _contains_loop(n.else_)):
            return True
        if isinstance(n, Let) and isinstance(n.expr, (Join, If)):
            return True
    return False


def _uses_whole(nodes: list[BodyNode], name: str) -> bool:
    """True if `name` is referenced as a whole value (not only via .field)."""

    def expr_whole(e: Expr) -> bool:
        if isinstance(e, VarRef):
            return e.name == name
        if isinstance(e, ElemAccess):
            if isinstance(e.base, VarRef) and isinstance(e.accessor, str):
                return False  # field access, not whole use
            return expr_whole(e.base)
        if isinstance(e, Concat):
            return any(expr_whole(a) for a in e.args)
        if isinstance(e, Has):
            a = e.arg
            return (
                isinstance(a, ElemAccess)
                and isinstance(a.base, VarRef)
                and a.base.name == name
            )
        if isinstance(e, (RemoveParens, Stringize, Len, IsParen, IsEmpty, ToSeq, ToTuple)):
            return expr_whole(e.arg)
        return False

    def walk(nodes: list[BodyNode]) -> bool:
        for n in nodes:
            if isinstance(n, Interp) and expr_whole(n.expr):
                return True
            if isinstance(n, Let):
                if isinstance(n.expr, (Join, If)):
                    if walk([n.expr]):
                        return True
                elif expr_whole(n.expr):
                    return True
            if isinstance(n, (ForEach, Join)):
                if n.iterable == name or walk(n.body):
                    return True
            if isinstance(n, If):
                cond_expr = (
                    n.cond.arg if isinstance(n.cond, (IsParen, IsEmpty, Has)) else n.cond.lhs
                )
                if expr_whole(cond_expr) or walk(n.then) or walk(n.else_):
                    return True
        return False

    return walk(nodes)


# C_LITERAL_PATTERN lives in parser.py (the directive scanners embed it);
# re-exported here for collapse.py and the tests.
_LITERAL_RE = re.compile(C_LITERAL_PATTERN)


def _word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _join_segments(parts: list[str]) -> str:
    """Join rendered segments, inserting a space where two word characters
    would otherwise fuse across a segment boundary: uncursed-pp never
    token-pastes implicitly - concat() is the explicit paste."""
    acc = ""
    for part in parts:
        if acc and part and _word_char(acc[-1]) and _word_char(part[0]):
            acc += " "
        acc += part
    return acc


def _collapse_ws(text: str) -> str:
    """Collapse whitespace runs, but never inside string/char literals."""
    parts: list[str] = []
    last = 0
    for m in _LITERAL_RE.finditer(text):
        parts.append(re.sub(r"\s+", " ", text[last : m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(re.sub(r"\s+", " ", text[last:]))
    return "".join(parts).strip()


def _format_define(head: str, body: str) -> str:
    lines = [_collapse_ws(line) for line in body.split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        return f"#define {head}\n"
    if len(lines) == 1:
        return f"#define {head} {lines[0]}\n"
    joined = " \\\n    ".join(lines)
    return f"#define {head} \\\n    {joined}\n"


def _source_comment(source: str) -> str:
    """The original .uncursed text, embedded above the macro's #defines."""
    lines = [line.rstrip().replace("*/", "* /") for line in source.splitlines()]
    body = "".join(f" * {line}\n" if line else " *\n" for line in lines)
    return f"/* uncursed-pp source:\n{body} */\n"


def _standalone_comment(text: str) -> str:
    """A detached top-level comment group (incl. #?/#=> spec lines),
    reproduced verbatim at its source position."""
    lines = [line.rstrip().replace("*/", "* /") for line in text.splitlines()]
    if len(lines) == 1:
        return f"/* {lines[0]} */\n"
    head, *rest = lines
    body = "".join(f" * {line}\n" if line else " *\n" for line in rest)
    return f"/* {head}\n{body} */\n"


def _format_helper(helper: _Helper) -> str:
    body = f" {helper.body}" if helper.body else ""
    return f"#define {helper.name}({helper.params}){body}\n"


def emit_file(file: File, *, source_name: str, config: EmitConfig | None = None) -> str:
    return _emit_file(file, source_name, config or EmitConfig())[0]


def _emit_file(file: File, source_name: str, config: EmitConfig) -> tuple[str, dict[str, Any]]:
    """emit_file plus the file_state the emitters accumulated (kw_utils,
    chain_tables) - what compile_template needs to build the companion."""
    from .collapse import collapse
    stem = re.sub(r"[^A-Za-z0-9]", "_", source_name.removesuffix(".uncursed")).upper()
    used: set[str] = set()
    file_state: dict[str, Any] = {"kw_utils": False, "chain_tables": set()}
    outs = [
        _MacroEmitter(macro, config, source_name, used, file_state).emit()
        for macro in file.macros
    ]
    collapse(outs, f"{config.helper_prefix}{stem}_")
    macro_chunks: list[str] = []

    def emit_standalone(position: int) -> None:
        for pos, text in file.comments:
            if pos == position:
                macro_chunks.append("\n")
                macro_chunks.append(_standalone_comment(text))
        for pos, text in file.directives:
            if pos == position:
                macro_chunks.append("\n")
                macro_chunks.append(text + "\n")

    for idx, (macro, out) in enumerate(zip(file.macros, outs)):
        emit_standalone(idx)
        macro_chunks.append("\n")
        if macro.source:
            macro_chunks.append(_source_comment(macro.source))
        for helper in out.helpers:
            macro_chunks.append(_format_helper(helper))
        macro_chunks.extend(out.defines)
    emit_standalone(len(file.macros))

    if config.pp_include is not None:
        includes = [config.pp_include]
    else:
        includes = sorted(f"{config.pp_include_dir}/{_PP_HEADERS[name]}" for name in used)

    chunks = [
        f"/* Generated by uncursed-pp from {source_name} — do not edit. */\n",
        "#pragma once\n",
    ]
    if includes:
        chunks.append("\n")
        chunks.extend(f"#include <{inc}>\n" for inc in includes)
    if file_state["kw_utils"] or file_state["chain_tables"]:
        chunks.append(f"#include {runtime_include_text(config)}\n")
    for extra in config.extra_includes:
        if extra.startswith("<"):
            chunks.append(f"#include {extra}\n")
        else:
            chunks.append(f'#include "{extra}"\n')
    chunks.extend(macro_chunks)
    return "".join(chunks), file_state


def runtime_name(config: EmitConfig) -> str:
    """Filename of the companion runtime header."""
    if config.runtime_name is not None:
        return config.runtime_name
    slug = config.helper_prefix.lower().rstrip("_")
    return f"{slug}_runtime.h"


def runtime_include_text(config: EmitConfig) -> str:
    """The token generated headers #include for the companion runtime:
    `<...>` kept verbatim, anything else quoted. Fully decoupled from
    where the file is written (runtime_name) - resolving a custom path
    is the caller's include-path contract."""
    if config.runtime_include is not None:
        inc = config.runtime_include
        return inc if inc.startswith("<") else f'"{inc}"'
    return f'"{runtime_name(config)}"'


def runtime_header(
    config: EmitConfig, *, chain_limits: Iterable[int] | None = None
) -> str:
    """Common macros shared by all generated headers (keyword-arg utilities
    and the loop-chain size-class tables).

    Generated files #include this by name; regenerate it alongside them.
    `chain_limits` selects which LE<K> tables to emit (one per K a sharing
    header may need); the default is the default loop_chain_limit, 16.
    """
    hp = config.helper_prefix
    limits = sorted(set(chain_limits)) if chain_limits is not None else [16]
    parts = [
        "/* Common runtime macros, shared by all uncursed-pp-generated headers.\n"
        "   Generated by uncursed-pp — do not edit. */\n"
        "#pragma once\n"
        "\n"
        f"#define {hp}KW_SPREAD(...) __VA_ARGS__\n"
    ]
    for k in limits:
        parts.append(
            f"\n/* size-class table: LE{k}_<n> is 1 iff n <= {k} (loop chain guard) */\n"
        )
        parts.append(
            "".join(f"#define {hp}LE{k}_{n} {1 if n <= k else 0}\n" for n in range(1, 257))
        )
    return "".join(parts)


@dataclass
class CompileResult:
    header: str
    runtime: str | None  # companion runtime header content, if the header needs it
    runtime_name: str
    config: EmitConfig  # resolved config (pragmas applied)
    chain_limits: tuple[int, ...] = ()  # LE<K> tables the runtime carries


def compile_template(
    source: str, filename: str, *, config: EmitConfig | None = None
) -> CompileResult:
    """Full pipeline: meta-template -> parse -> emit header (+ companion
    runtime if needed)."""
    file = parse_file(render_meta(source, filename), filename)
    config = _apply_pragmas(config or EmitConfig(), file.pragmas)
    config = replace(
        config, extra_includes=config.extra_includes + tuple(file.extra_includes)
    )
    if (
        config.pp_prefix != "BOOST_PP_"
        and config.pp_include is None
        and config.pp_include_dir == "boost/preprocessor"
    ):
        raise UncursedPpError(
            "a custom pp_prefix needs pp_include or pp_include_dir (default "
            "granular boost includes only fit the default prefix)",
            filename,
            0,
        )
    header, state = _emit_file(file, filename.rsplit("/", 1)[-1], config)
    needs_runtime = state["kw_utils"] or state["chain_tables"]
    # written companions always carry the default table too: runtimes from
    # different templates land on the same filename, so each must stay a
    # superset of the common case
    limits = tuple(sorted({16} | state["chain_tables"]))
    return CompileResult(
        header=header,
        runtime=runtime_header(config, chain_limits=limits) if needs_runtime else None,
        runtime_name=runtime_name(config),
        chain_limits=limits if needs_runtime else (),
        config=config,
    )


def compile_source(source: str, filename: str, *, config: EmitConfig | None = None) -> str:
    """Compile and return just the header text. Convenience for tests."""
    return compile_template(source, filename, config=config).header


def _apply_pragmas(config: EmitConfig, pragmas: dict[str, str]) -> EmitConfig:
    return replace(
        config,
        pp_prefix=pragmas.get("pp_prefix", config.pp_prefix),
        pp_include=pragmas.get("pp_include", config.pp_include),
        pp_include_dir=pragmas.get("pp_include_dir", config.pp_include_dir),
        helper_prefix=pragmas.get("helper_prefix", config.helper_prefix),
        runtime_name=pragmas.get("runtime_name", config.runtime_name),
        runtime_include=pragmas.get("runtime_include", config.runtime_include),
        arg_prefix=pragmas.get("arg_prefix", config.arg_prefix),
        loop_chain=pragmas.get("loop_chain", "on" if config.loop_chain else "off") != "off",
        loop_chain_limit=int(pragmas.get("loop_chain_limit", config.loop_chain_limit)),
    )
