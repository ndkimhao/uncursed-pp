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
    pp_include: str | None = None  # None = granular boost includes from usage
    helper_prefix: str = "CURSEDPP_"


# Header (under boost/preprocessor/) providing each primitive we may emit.
_PP_HEADERS = {
    "CAT": "cat.hpp",
    "SEQ_FOR_EACH": "seq/for_each.hpp",
    "SEQ_FOR_EACH_I": "seq/for_each_i.hpp",
    "SEQ_ELEM": "seq/elem.hpp",
    "SEQ_SIZE": "seq/size.hpp",
    "SEQ_FOLD_LEFT": "seq/fold_left.hpp",
    "TUPLE_ELEM": "tuple/elem.hpp",
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
            # the body sees the variadic tail as a token seq
            env[p.name] = _Binding(
                f"{self.pp('VARIADIC_TO_SEQ')}(__VA_ARGS__)", SeqT(TokenT())
            )
        body = self._render_block(self.macro.body, env)

        if defaulted:
            self._emit_overload_chain(pos, defaulted, body)
        elif named:
            self._emit_named(pos, named, body)
        else:
            heads = ["..." if isinstance(p.type, VariadicT) else p.name for p in self.macro.params]
            self.out.defines.append(_format_define(f"{self.macro.name}({', '.join(heads)})", body))
        return self.out

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
        cat = self.pp("CAT")
        iif = self.pp("IIF")

        defines = self.out.defines
        for p in named:
            kw = p.name
            probe = f"{base}KW_{kw}_"
            defines.append(f"#define {probe}{kw}(v) v, 1\n")
            defines.append(f"#define {base}IS_{kw}(e) {kw_util}KW_CHECK({cat}({probe}, e))\n")
            defines.append(
                f"#define {base}TAKE_{kw}(state, e) {kw_util}KW_FIRST({cat}({probe}, e))\n"
            )
            defines.append(
                f"#define {base}FOLD_{kw}(s, state, e) "
                f"{iif}({base}IS_{kw}(e), {base}TAKE_{kw}, {base}KEEP)(state, e)\n"
            )
            defines.append(
                f"#define {base}GET_{kw}(seq) "
                f"{self.pp('SEQ_FOLD_LEFT')}({base}FOLD_{kw}, {p.default or ''}, seq)\n"
            )
        defines.append(f"#define {base}KEEP(state, e) state\n")
        defines.append(_format_define(f"{base}BODY({', '.join(all_names)})", body))

        gets = ", ".join(
            f"{base}GET_{p.name}({self.pp('VARIADIC_TO_SEQ')}(__VA_ARGS__))" for p in named
        )
        defines.append(
            f"#define {base}KW({', '.join(pos_names)}, ...) "
            f"{base}BODY({', '.join(pos_names)}, {gets})\n"
        )
        required = len(pos)
        default_args = ", ".join(p.default or "" for p in named)
        defines.append(
            f"#define {base}{required}({', '.join(pos_names)}) "
            f"{base}BODY({', '.join(pos_names)}, {default_args})\n"
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
            elif isinstance(e, (RemoveParens, Len, IsParen)):
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

    def _render_foreach(self, loop: ForEach, env: _Env) -> str:
        seq_expr, elem_type = self._iterable_binding(loop.iterable, env, loop)
        loop_env = self._loop_env(env, loop.unpack, loop.var, elem_type, loop)
        body = _collapse_ws(self._render_block(loop.body, loop_env))
        helper = self._add_helper("EACH", "r, d, e", body)
        return f"{self.pp('SEQ_FOR_EACH')}({helper}, ~, {seq_expr})"

    def _render_join(self, join: Join, env: _Env) -> str:
        seq_expr, elem_type = self._iterable_binding(join.iterable, env, join)
        loop_env = self._loop_env(env, None, join.var, elem_type, join)
        body = _collapse_ws(self._render_block(join.body, loop_env))
        sep = join.sep.strip()
        if sep == ",":
            each_body = f"{self.pp('COMMA_IF')}(i) {body}"
        else:
            sep_helper = self._add_helper("SEP", "", sep)
            each_body = f"{self.pp('IF')}(i, {sep_helper}, {self.pp('EMPTY')})() {body}"
        helper = self._add_helper("EACH", "r, d, i, e", each_body)
        return f"{self.pp('SEQ_FOR_EACH_I')}({helper}, ~, {seq_expr})"

    def _add_helper(self, kind: str, params: str, body: str) -> str:
        count = self._helper_counts.get(kind, 0) + 1
        self._helper_counts[kind] = count
        name = f"{self.config.helper_prefix}{self.macro.name}_{kind}{count}"
        self.out.helpers.append(_Helper(name=name, params=params, body=body))
        return name


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
    for out in outs:
        macro_chunks.append("\n")
        for helper in out.helpers:
            macro_chunks.append(_format_helper(helper))
        macro_chunks.extend(out.defines)

    if config.pp_include is not None:
        includes = [config.pp_include]
    else:
        includes = sorted("boost/preprocessor/" + _PP_HEADERS[name] for name in used)

    chunks = [
        f"/* Generated by cursedpp from {source_name} — do not edit. */\n",
        "#pragma once\n",
    ]
    if includes:
        chunks.append("\n")
        chunks.extend(f"#include <{inc}>\n" for inc in includes)
    if file_state["kw_utils"]:
        chunks.append(f'#include "{runtime_name(config)}"\n')
    chunks.extend(macro_chunks)
    return "".join(chunks)


def runtime_name(config: EmitConfig) -> str:
    """Filename of the companion runtime header, derived from helper_prefix."""
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
        f"#define {hp}KW_CHECK_N(x, n, ...) n\n"
        f"#define {hp}KW_CHECK(...) {hp}KW_CHECK_N(__VA_ARGS__, 0,)\n"
        f"#define {hp}KW_FIRST_N(x, ...) x\n"
        f"#define {hp}KW_FIRST(...) {hp}KW_FIRST_N(__VA_ARGS__,)\n"
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
    if config.pp_prefix != "BOOST_PP_" and config.pp_include is None:
        raise CursedppError(
            "a custom pp_prefix needs pp_include (granular boost includes "
            "only fit the default prefix)",
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
        helper_prefix=pragmas.get("helper_prefix", config.helper_prefix),
    )
