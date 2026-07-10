"""Emit Boost.PP C header text from the cursedpp AST."""

from __future__ import annotations

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
    If,
    Interp,
    IsParen,
    Join,
    Len,
    Let,
    MacroDef,
    Param,
    RemoveParens,
    SeqT,
    Stringize,
    Text,
    TokenT,
    TupleT,
    Type,
    VariadicT,
    VarRef,
)
from .parser import CursedppError, parse_file


@dataclass
class EmitConfig:
    pp_prefix: str = "BOOST_PP_"
    pp_include: str | None = None  # None = granular includes from usage
    pp_include_dir: str = "boost/preprocessor"  # root of granular includes
    helper_prefix: str = "CURSEDPP_"
    runtime_name: str | None = None  # None = derived from helper_prefix
    extra_includes: tuple[str, ...] = ()  # user includes appended verbatim


# Header (under boost/preprocessor/) providing each primitive we may emit.
_PP_HEADERS = {
    "CAT": "cat.hpp",
    "SEQ_FOR_EACH": "seq/for_each.hpp",
    "SEQ_FOR_EACH_I": "seq/for_each_i.hpp",
    "SEQ_ELEM": "seq/elem.hpp",
    "SEQ_SIZE": "seq/size.hpp",
    "SEQ_FOLD_LEFT": "seq/fold_left.hpp",
    "TUPLE_ELEM": "tuple/elem.hpp",
    "STRINGIZE": "stringize.hpp",
    "COMMA_IF": "punctuation/comma_if.hpp",
    "COMMA": "punctuation/comma.hpp",
    "REMOVE_PARENS": "punctuation/remove_parens.hpp",
    "IS_BEGIN_PARENS": "punctuation/is_begin_parens.hpp",
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


@dataclass
class _MacroOut:
    helpers: list[_Helper] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Binding:
    c_expr: str
    type: Type | None  # None/TokenT = plain token, arity/shape unknown
    spread: bool = False  # tuple param whose fields are direct BODY params


_Env = dict[str, _Binding]


class _MacroEmitter:
    """Emits one macro: a main #define plus any generated helper macros."""

    def __init__(
        self,
        macro: MacroDef,
        config: EmitConfig,
        filename: str,
        used: set[str],
        file_state: dict[str, bool],
    ):
        self.macro = macro
        self.config = config
        self.filename = filename
        self.used = used
        self.file_state = file_state
        self.out = _MacroOut()
        self._helper_counts: dict[str, int] = {}
        self._loop_depth = 0

    def pp(self, name: str) -> str:
        self.used.add(name)
        return f"{self.config.pp_prefix}{name}"

    def emit(self) -> _MacroOut:
        pos = [p for p in self.macro.params if not p.named and p.default is None]
        defaulted = [p for p in self.macro.params if not p.named and p.default is not None]
        named = [p for p in self.macro.params if p.named]
        variadic = [p for p in self.macro.params if isinstance(p.type, VariadicT)]
        self._validate_params(pos, defaulted, named, variadic)

        env = {p.name: _Binding(p.name, p.type) for p in self.macro.params}
        for p in variadic:
            # the body sees the variadic tail as a seq of the element type
            assert isinstance(p.type, VariadicT)
            env[p.name] = _Binding(
                f"{self.pp('VARIADIC_TO_SEQ')}(__VA_ARGS__)", SeqT(p.type.elem)
            )
        for p in named:
            if p.variadic_value:
                # value travels parenthesized; interpolation auto-unwraps
                env[p.name] = _Binding(f"{self.pp('REMOVE_PARENS')}({p.name})", TokenT())
        spread_params: set[str] = set()
        if not defaulted and not named:
            spread_params = self._spreadable_tuple_params()
            for name in spread_params:
                env[name] = _Binding(name, env[name].type, spread=True)
        body = self._render_block(self.macro.body, env)

        if defaulted:
            self._emit_overload_chain(pos, defaulted, body)
        elif named:
            self._emit_named(pos, named, body)
        elif spread_params:
            self._emit_spread_body(spread_params, body)
        else:
            heads = ["..." if isinstance(p.type, VariadicT) else p.name for p in self.macro.params]
            self.out.defines.append(_format_define(f"{self.macro.name}({', '.join(heads)})", body))
        return self.out

    def _spreadable_tuple_params(self) -> set[str]:
        """Tuple params whose fields can become direct BODY parameters."""
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
        for p in self.macro.params:
            if p.name in spread_params:
                assert isinstance(p.type, TupleT)
                body_params.extend(p.type.names)
                args.append(f"{spread} {p.name}")
            else:
                body_params.append(p.name)
                args.append(p.name)
        self.out.defines.append(
            _format_define(f"{base}BODY1({', '.join(body_params)})", body)
        )
        self.out.defines.append(f"#define {base}BODY1_D(...) {base}BODY1(__VA_ARGS__)\n")
        heads = ", ".join(p.name for p in self.macro.params)
        self.out.defines.append(
            f"#define {self.macro.name}({heads}) {base}BODY1_D({', '.join(args)})\n"
        )

    def _validate_params(
        self,
        pos: list[Param],
        defaulted: list[Param],
        named: list[Param],
        variadic: list[Param],
    ) -> None:
        line = self.macro.line
        if variadic:
            if len(variadic) > 1:
                raise CursedppError("at most one variadic parameter", self.filename, line)
            if not isinstance(self.macro.params[-1].type, VariadicT):
                raise CursedppError(
                    "the variadic parameter must be last", self.filename, line
                )
            if defaulted or named:
                raise CursedppError(
                    "a variadic parameter excludes tail defaults and named params",
                    self.filename,
                    line,
                )
        if defaulted and named:
            raise CursedppError(
                "a macro may use tail defaults OR named params, not both",
                self.filename,
                line,
            )
        seen_special = False
        for p in self.macro.params:
            special = p.named or p.default is not None
            if seen_special and not special:
                raise CursedppError(
                    f"required parameter {p.name!r} cannot follow a defaulted/named one",
                    self.filename,
                    line,
                )
            seen_special = seen_special or special
        if (defaulted or named) and not pos:
            raise CursedppError(
                "defaults/named params need at least one required parameter "
                "(arity dispatch cannot see a zero-argument call)",
                self.filename,
                line,
            )

    def _emit_overload_chain(
        self, pos: list[Param], defaulted: list[Param], body: str
    ) -> None:
        all_params = pos + defaulted
        names = [p.name for p in all_params]
        base = f"{self.config.helper_prefix}{self.macro.name}_"
        total, required = len(all_params), len(pos)
        for k in range(required, total):
            head = f"{base}{k}({', '.join(names[:k])})"
            args = names[:k] + [p.default or "" for p in all_params[k:]]
            self.out.defines.append(f"#define {head} {base}{total}({', '.join(args)})\n")
        self.out.defines.append(_format_define(f"{base}{total}({', '.join(names)})", body))
        self.out.defines.append(
            f"#define {self.macro.name}(...) "
            f"{self.pp('OVERLOAD')}({base}, __VA_ARGS__)(__VA_ARGS__)\n"
        )

    def _emit_named(self, pos: list[Param], named: list[Param], body: str) -> None:
        self.file_state["kw_utils"] = True
        base = f"{self.config.helper_prefix}{self.macro.name}_"
        kw_util = self.config.helper_prefix
        pos_names = [p.name for p in pos]
        all_names = pos_names + [p.name for p in named]
        defaults = ", ".join(
            f"({p.default or ''})" if p.variadic_value else (p.default or "")
            for p in named
        )
        defines = self.out.defines

        # each keyword argument dispatches itself: CAT(SET_, WIDTH(20))
        # -> SET_WIDTH(20) -> "0, 20" (slot index, value); one fold
        # TUPLE_REPLACEs slots in the defaults tuple. 'named variadic'
        # setters capture bare commas and re-wrap so the value stays one
        # macro argument.
        for slot, p in enumerate(named):
            if p.variadic_value:
                defines.append(f"#define {base}SET_{p.name}(...) {slot}, (__VA_ARGS__)\n")
            else:
                defines.append(f"#define {base}SET_{p.name}(v) {slot}, v\n")
        # STEP re-parses the expanded "slot, value" pair, then dispatches to
        # a generated per-slot replacer - TUPLE_REPLACE would run a full
        # BOOST_PP_WHILE per keyword argument (measured 22x slower).
        defines.append(
            f"#define {base}STEP(s, state, e) "
            f"{base}STEP_D(state, {self.pp('CAT')}({base}SET_, e))\n"
        )
        defines.append(f"#define {base}STEP_D(state, ...) {base}STEP_I(state, __VA_ARGS__)\n")
        defines.append(
            f"#define {base}STEP_I(state, i, v) {self.pp('CAT')}({base}PUT_, i)(v, state)\n"
        )
        arity = len(named)
        slots = [f"p{j}" for j in range(arity)]
        for slot in range(arity):
            replaced = ", ".join("v" if j == slot else f"p{j}" for j in range(arity))
            defines.append(
                f"#define {base}PUT_{slot}(v, state) "
                f"{base}PUT_{slot}_D(v, {kw_util}KW_SPREAD state)\n"
            )
            defines.append(f"#define {base}PUT_{slot}_D(...) {base}PUT_{slot}_I(__VA_ARGS__)\n")
            defines.append(f"#define {base}PUT_{slot}_I(v, {', '.join(slots)}) ({replaced})\n")
        defines.append(_format_define(f"{base}BODY({', '.join(all_names)})", body))
        unpack_args = ", ".join(
            f"{self.pp('TUPLE_ELEM')}({slot}, state)" for slot in range(len(named))
        )
        defines.append(
            f"#define {base}UNPACK({', '.join(pos_names)}, state) "
            f"{base}BODY({', '.join(pos_names)}, {unpack_args})\n"
        )
        defines.append(
            f"#define {base}KW({', '.join(pos_names)}, ...) "
            f"{base}UNPACK({', '.join(pos_names)}, "
            f"{self.pp('SEQ_FOLD_LEFT')}({base}STEP, ({defaults}), "
            f"{self.pp('VARIADIC_TO_SEQ')}(__VA_ARGS__)))\n"
        )
        required = len(pos)
        defines.append(
            f"#define {base}{required}({', '.join(pos_names)}) "
            f"{base}BODY({', '.join(pos_names)}, {defaults})\n"
        )
        for i in range(1, len(named) + 1):
            defines.append(f"#define {base}{required + i} {base}KW\n")
        defines.append(
            f"#define {self.macro.name}(...) "
            f"{self.pp('OVERLOAD')}({base}, __VA_ARGS__)(__VA_ARGS__)\n"
        )

    # ── rendering ────────────────────────────────────────────────────

    def _render_block(self, nodes: list[BodyNode], env: _Env) -> str:
        env = dict(env)  # @let bindings stay local to this block
        parts: list[str] = []
        for node in nodes:
            if isinstance(node, Text):
                parts.append(node.value)
            elif isinstance(node, Interp):
                parts.append(self._resolve(node.expr, env, node.line).c_expr)
            elif isinstance(node, ForEach):
                parts.append(self._render_foreach(node, env))
                parts.append("\n")
            elif isinstance(node, Join):
                parts.append(self._render_join(node, env))
            elif isinstance(node, If):
                parts.append(self._render_if(node, env))
            elif isinstance(node, Let):
                if isinstance(node.expr, Join):
                    env[node.name] = _Binding(self._render_join(node.expr, env), TokenT())
                elif isinstance(node.expr, If):
                    env[node.name] = _Binding(self._render_if(node.expr, env), TokenT())
                else:
                    env[node.name] = self._resolve(node.expr, env, node.line)
            else:  # pragma: no cover - future node kinds
                raise NotImplementedError(f"cannot emit {node!r}")
        return "".join(parts)

    def _resolve(self, expr: Expr, env: _Env, line: int, allow_literal: bool = False) -> _Binding:
        if isinstance(expr, VarRef):
            if expr.name in env:
                return env[expr.name]
            if allow_literal:
                return _Binding(expr.name, TokenT())
            raise CursedppError(f"undefined variable: {expr.name}", self.filename, line)
        if isinstance(expr, ElemAccess):
            return self._resolve_access(expr, env, line)
        if isinstance(expr, Concat):
            rendered = [self._resolve(a, env, line, allow_literal=True).c_expr for a in expr.args]
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
            if not isinstance(inner.type, SeqT):
                raise CursedppError("len() needs a seq-typed value", self.filename, line)
            return _Binding(f"{self.pp('SEQ_SIZE')}({inner.c_expr})", TokenT())
        if isinstance(expr, IsParen):
            inner = self._resolve(expr.arg, env, line)
            return _Binding(f"{self.pp('IS_BEGIN_PARENS')}({inner.c_expr})", TokenT())
        raise NotImplementedError(f"cannot emit expression {expr!r}")  # pragma: no cover

    def _resolve_access(self, expr: ElemAccess, env: _Env, line: int) -> _Binding:
        base = self._resolve(expr.base, env, line)
        if isinstance(expr.accessor, str):
            if not isinstance(base.type, TupleT):
                raise CursedppError(
                    f"named element access '.{expr.accessor}' needs a tuple-typed value",
                    self.filename,
                    line,
                )
            if expr.accessor not in base.type.names:
                raise CursedppError(
                    f"tuple has no element {expr.accessor!r} (has: {', '.join(base.type.names)})",
                    self.filename,
                    line,
                )
            if base.spread:
                return _Binding(expr.accessor, TokenT())
            idx = base.type.names.index(expr.accessor)
            return _Binding(f"{self.pp('TUPLE_ELEM')}({idx}, {base.c_expr})", TokenT())
        if not isinstance(base.type, SeqT):
            raise CursedppError(
                f"indexed access '[{expr.accessor}]' needs a seq-typed value",
                self.filename,
                line,
            )
        return _Binding(f"{self.pp('SEQ_ELEM')}({expr.accessor}, {base.c_expr})", base.type.elem)

    # ── conditionals ─────────────────────────────────────────────────

    def _render_if(self, node: If, env: _Env) -> str:
        free = self._free_vars(node.then, env, set())
        for name in self._free_vars(node.else_, env, set()):
            if name not in free:
                free.append(name)
        branch_env = {n: _Binding(n, env[n].type) for n in free}
        params = ", ".join(free)

        then_helper = self._add_helper(
            "THEN", params, _collapse_ws(self._render_block(node.then, branch_env))
        )
        else_helper = self._add_helper(
            "ELSE", params, _collapse_ws(self._render_block(node.else_, branch_env))
        )
        cond = self._render_cond(node.cond, env, node.line)
        args = ", ".join(env[n].c_expr for n in free)
        return f"{self.pp('IIF')}({cond}, {then_helper}, {else_helper})({args})"

    def _render_cond(self, cond: Cond, env: _Env, line: int) -> str:
        if isinstance(cond, IsParen):
            return self._resolve(cond, env, line).c_expr
        lhs = self._resolve(cond.lhs, env, line).c_expr
        return f"{self.pp(_OP_TO_PP[cond.op])}({lhs}, {cond.value})"

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
            elif isinstance(e, (RemoveParens, Stringize, Len, IsParen)):
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
            if not unpack and binding and isinstance(binding.type, SeqT):
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
                    if isinstance(n.cond, IsParen):
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
    ) -> _Env:
        loop_env = dict(env)
        if unpack is not None:
            if not isinstance(elem_type, TupleT):
                raise CursedppError("tuple unpacking needs a seq of tuples", self.filename, node.line)
            if len(unpack) != len(elem_type.names):
                raise CursedppError(
                    f"unpack arity {len(unpack)} != tuple arity {len(elem_type.names)}",
                    self.filename,
                    node.line,
                )
            names: tuple[str, ...] = unpack
        elif var is not None:
            loop_env[var] = _Binding("e", elem_type)
            names = ()
        else:
            names = elem_type.names if isinstance(elem_type, TupleT) else ()
        for idx, name in enumerate(names):
            loop_env[name] = _Binding(f"{self.pp('TUPLE_ELEM')}({idx}, e)", TokenT())
        return loop_env

    def _iterable_binding(self, name: str, env: _Env, node: ForEach | Join) -> tuple[str, Type]:
        """Resolve an iterated name to (c_expr, element type)."""
        binding = env.get(name)
        if binding is None:
            raise CursedppError(f"undefined variable: {name}", self.filename, node.line)
        if not isinstance(binding.type, SeqT):
            raise CursedppError(f"cannot iterate non-seq {name!r}", self.filename, node.line)
        return binding.c_expr, binding.type.elem

    def _loop_data(
        self, node: ForEach | Join, env: _Env, loop_env: _Env
    ) -> tuple[str, _Env]:
        """Free outer variables in a loop body ride FOR_EACH's `d` slot.

        Returns (data argument for the call site, adjusted loop env).
        One free variable travels as `d` itself; several as a tuple in `d`.
        """
        # bound = names the loop itself introduced or rebound (unpack names,
        # `as` var, implicit tuple fields); everything else from the outer
        # env is free and must travel through `d`.
        bound = {name for name, binding in loop_env.items() if env.get(name) != binding}
        free = self._free_vars(node.body, env, bound)
        if not free:
            return "~", loop_env
        loop_env = dict(loop_env)
        if len(free) == 1:
            data = env[free[0]].c_expr
            loop_env[free[0]] = _Binding("d", env[free[0]].type)
        else:
            data = "(" + ", ".join(env[n].c_expr for n in free) + ")"
            for idx, name in enumerate(free):
                loop_env[name] = _Binding(
                    f"{self.pp('TUPLE_ELEM')}({idx}, d)", env[name].type
                )
        return data, loop_env

    def _reject_nested_loop(self, node: ForEach | Join) -> None:
        if self._loop_depth:
            raise CursedppError(
                "nested loops (@for/@join inside a loop body) are not "
                "supported: BOOST_PP_SEQ_FOR_EACH cannot re-enter itself. "
                "Flatten the data or split the inner loop into its own macro "
                "invoked outside the loop.",
                self.filename,
                node.line,
            )

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
        loop_env = self._loop_env(env, unpack, node.var, elem_type, node)
        data, loop_env = self._loop_data(node, env, loop_env)

        field_names: tuple[str, ...] = ()
        if node.var is None and isinstance(elem_type, TupleT):
            field_names = unpack or elem_type.names
        # free vars are the ones _loop_data rebound onto the d slot,
        # kept in slot order
        free = [n for n, b in loop_env.items() if b.c_expr == "d" or b.c_expr.endswith(", d)")]
        free.sort(key=lambda n: int(m.group(1)) if (m := re.search(r"\((\d+), d\)", loop_env[n].c_expr)) else 0)
        ap_params = free + list(field_names)

        use_ap = bool(field_names) and len(set(ap_params)) == len(ap_params)
        render_env = loop_env
        if use_ap:
            render_env = dict(loop_env)
            for name in ap_params:
                render_env[name] = _Binding(name, loop_env[name].type)
        self._loop_depth += 1
        try:
            body = _collapse_ws(self._render_block(node.body, render_env))
        finally:
            self._loop_depth -= 1
        if not use_ap:
            return body, data

        ap = self._add_helper("AP", ", ".join(ap_params), body)
        if not free:
            return f"{ap} e", data
        self.file_state["kw_utils"] = True
        spread = f"{self.config.helper_prefix}KW_SPREAD"
        d_part = "d" if len(free) == 1 else f"{spread} d"
        self.out.helpers.append(
            _Helper(name=f"{ap}_D", params="...", body=f"{ap}(__VA_ARGS__)")
        )
        return f"{ap}_D({d_part}, {spread} e)", data

    def _render_foreach(self, loop: ForEach, env: _Env) -> str:
        self._reject_nested_loop(loop)
        seq_expr, elem_type = self._iterable_binding(loop.iterable, env, loop)
        body, data = self._loop_body(loop, env, elem_type)
        helper = self._add_helper("EACH", "r, d, e", body)
        return f"{self.pp('SEQ_FOR_EACH')}({helper}, {data}, {seq_expr})"

    def _render_join(self, join: Join, env: _Env) -> str:
        self._reject_nested_loop(join)
        seq_expr, elem_type = self._iterable_binding(join.iterable, env, join)
        body, data = self._loop_body(join, env, elem_type)
        sep = join.sep.strip()
        if sep == ",":
            each_body = f"{self.pp('COMMA_IF')}(i) {body}"
        else:
            sep_helper = self._add_helper("SEP", "", sep)
            each_body = f"{self.pp('IF')}(i, {sep_helper}, {self.pp('EMPTY')})() {body}"
        helper = self._add_helper("EACH", "r, d, i, e", each_body)
        return f"{self.pp('SEQ_FOR_EACH_I')}({helper}, {data}, {seq_expr})"

    def _add_helper(self, kind: str, params: str, body: str) -> str:
        count = self._helper_counts.get(kind, 0) + 1
        self._helper_counts[kind] = count
        name = f"{self.config.helper_prefix}{self.macro.name}_{kind}{count}"
        self.out.helpers.append(_Helper(name=name, params=params, body=body))
        return name


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
        if isinstance(e, (RemoveParens, Stringize, Len, IsParen)):
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
                cond_expr = n.cond.arg if isinstance(n.cond, IsParen) else n.cond.lhs
                if expr_whole(cond_expr) or walk(n.then) or walk(n.else_):
                    return True
        return False

    return walk(nodes)


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
    """The original .cursed text, embedded above the macro's #defines."""
    lines = [line.rstrip().replace("*/", "* /") for line in source.splitlines()]
    body = "".join(f" * {line}\n" if line else " *\n" for line in lines)
    return f"/* cursedpp source:\n{body} */\n"


def _format_helper(helper: _Helper) -> str:
    body = f" {helper.body}" if helper.body else ""
    return f"#define {helper.name}({helper.params}){body}\n"


def emit_file(file: File, *, source_name: str, config: EmitConfig | None = None) -> str:
    from .collapse import collapse

    config = config or EmitConfig()
    used: set[str] = set()
    file_state = {"kw_utils": False}
    outs = [
        _MacroEmitter(macro, config, source_name, used, file_state).emit()
        for macro in file.macros
    ]
    collapse(outs, config.helper_prefix)
    macro_chunks: list[str] = []
    for macro, out in zip(file.macros, outs):
        macro_chunks.append("\n")
        if macro.source:
            macro_chunks.append(_source_comment(macro.source))
        for helper in out.helpers:
            macro_chunks.append(_format_helper(helper))
        macro_chunks.extend(out.defines)

    if config.pp_include is not None:
        includes = [config.pp_include]
    else:
        includes = sorted(f"{config.pp_include_dir}/{_PP_HEADERS[name]}" for name in used)

    chunks = [
        f"/* Generated by cursedpp from {source_name} — do not edit. */\n",
        "#pragma once\n",
    ]
    if includes:
        chunks.append("\n")
        chunks.extend(f"#include <{inc}>\n" for inc in includes)
    if file_state["kw_utils"]:
        chunks.append(f'#include "{runtime_name(config)}"\n')
    for extra in config.extra_includes:
        if extra.startswith("<"):
            chunks.append(f"#include {extra}\n")
        else:
            chunks.append(f'#include "{extra}"\n')
    chunks.extend(macro_chunks)
    return "".join(chunks)


def runtime_name(config: EmitConfig) -> str:
    """Filename of the companion runtime header."""
    if config.runtime_name is not None:
        return config.runtime_name
    slug = config.helper_prefix.lower().rstrip("_")
    return f"{slug}_runtime.h"


def runtime_header(config: EmitConfig) -> str:
    """Common macros shared by all generated headers (keyword-arg utilities).

    Generated files #include this by name; regenerate it alongside them.
    """
    hp = config.helper_prefix
    return (
        "/* Common runtime macros, shared by all cursedpp-generated headers.\n"
        "   Generated by cursedpp — do not edit. */\n"
        "#pragma once\n"
        "\n"
        f"#define {hp}KW_SPREAD(...) __VA_ARGS__\n"
    )


@dataclass
class CompileResult:
    header: str
    runtime: str | None  # companion runtime header content, if the header needs it
    runtime_name: str


def compile_template(
    source: str, filename: str, *, config: EmitConfig | None = None
) -> CompileResult:
    """Full pipeline: parse -> emit header (+ companion runtime if needed)."""
    file = parse_file(source, filename)
    config = _apply_pragmas(config or EmitConfig(), file.pragmas)
    config = replace(
        config, extra_includes=config.extra_includes + tuple(file.extra_includes)
    )
    if (
        config.pp_prefix != "BOOST_PP_"
        and config.pp_include is None
        and config.pp_include_dir == "boost/preprocessor"
    ):
        raise CursedppError(
            "a custom pp_prefix needs pp_include or pp_include_dir (default "
            "granular boost includes only fit the default prefix)",
            filename,
            0,
        )
    header = emit_file(
        file,
        source_name=filename.rsplit("/", 1)[-1],
        config=config,
    )
    needs_runtime = f'#include "{runtime_name(config)}"' in header
    return CompileResult(
        header=header,
        runtime=runtime_header(config) if needs_runtime else None,
        runtime_name=runtime_name(config),
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
    )
