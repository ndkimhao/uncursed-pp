"""Emit Boost.PP C header text from the cursedpp AST."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .nodes import (
    Concat,
    ElemAccess,
    File,
    ForEach,
    Interp,
    Join,
    Let,
    MacroDef,
    RemoveParens,
    SeqT,
    Text,
    TokenT,
    TupleT,
    Type,
    VarRef,
)
from .parser import CursedppError, parse_file


@dataclass
class EmitConfig:
    pp_prefix: str = "BOOST_PP_"
    pp_include: str = "boost/preprocessor.hpp"


@dataclass
class _Helper:
    name: str
    params: str
    body: str


@dataclass
class _MacroOut:
    helpers: list[_Helper] = field(default_factory=list)
    define: str = ""


@dataclass(frozen=True)
class _Binding:
    c_expr: str
    type: Type | None  # None = plain token, arity/shape unknown


class _MacroEmitter:
    """Emits one macro: a main #define plus any generated helper macros."""

    def __init__(self, macro: MacroDef, config: EmitConfig, filename: str):
        self.macro = macro
        self.config = config
        self.filename = filename
        self.out = _MacroOut()
        self._helper_counts: dict[str, int] = {}

    def pp(self, name: str) -> str:
        return f"{self.config.pp_prefix}{name}"

    def emit(self) -> _MacroOut:
        params = ", ".join(p.name for p in self.macro.params)
        env = {p.name: _Binding(p.name, p.type) for p in self.macro.params}
        body = self._render_block(self.macro.body, env)
        self.out.define = _format_define(f"{self.macro.name}({params})", body)
        return self.out

    # ── rendering ────────────────────────────────────────────────────

    def _render_block(self, nodes: list, env: dict[str, _Binding]) -> str:
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
            elif isinstance(node, Let):
                env[node.name] = self._resolve(node.expr, env, node.line)
            else:  # pragma: no cover - future node kinds
                raise NotImplementedError(f"cannot emit {node!r}")
        return "".join(parts)

    def _resolve(self, expr, env: dict[str, _Binding], line: int, allow_literal: bool = False) -> _Binding:
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
        raise NotImplementedError(f"cannot emit expression {expr!r}")  # pragma: no cover

    def _resolve_access(self, expr: ElemAccess, env: dict[str, _Binding], line: int) -> _Binding:
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
            arity = len(base.type.names)
            idx = base.type.names.index(expr.accessor)
            return _Binding(f"{self.pp('TUPLE_ELEM')}({arity}, {idx}, {base.c_expr})", TokenT())
        if not isinstance(base.type, SeqT):
            raise CursedppError(
                f"indexed access '[{expr.accessor}]' needs a seq-typed value",
                self.filename,
                line,
            )
        return _Binding(f"{self.pp('SEQ_ELEM')}({expr.accessor}, {base.c_expr})", base.type.elem)

    # ── loops ────────────────────────────────────────────────────────

    def _loop_env(
        self,
        env: dict[str, _Binding],
        unpack: tuple[str, ...] | None,
        var: str | None,
        elem_type: Type,
        node,
    ) -> dict[str, _Binding]:
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
            names = unpack
        elif var is not None:
            loop_env[var] = _Binding("e", elem_type)
            names = elem_type.names if isinstance(elem_type, TupleT) else ()
        else:
            names = elem_type.names if isinstance(elem_type, TupleT) else ()
        arity = len(names)
        for idx, name in enumerate(names):
            loop_env[name] = _Binding(f"{self.pp('TUPLE_ELEM')}({arity}, {idx}, e)", TokenT())
        return loop_env

    def _iterable_binding(self, name: str, env: dict[str, _Binding], node) -> _Binding:
        binding = env.get(name)
        if binding is None:
            raise CursedppError(f"undefined variable: {name}", self.filename, node.line)
        if not isinstance(binding.type, SeqT):
            raise CursedppError(f"cannot iterate non-seq {name!r}", self.filename, node.line)
        return binding

    def _render_foreach(self, loop: ForEach, env: dict[str, _Binding]) -> str:
        seq = self._iterable_binding(loop.iterable, env, loop)
        loop_env = self._loop_env(env, loop.unpack, loop.var, seq.type.elem, loop)
        body = _collapse_ws(self._render_block(loop.body, loop_env))
        helper = self._add_helper("EACH", "r, d, e", body)
        return f"{self.pp('SEQ_FOR_EACH')}({helper}, ~, {seq.c_expr})"

    def _render_join(self, join: Join, env: dict[str, _Binding]) -> str:
        seq = self._iterable_binding(join.iterable, env, join)
        loop_env = self._loop_env(env, None, join.var, seq.type.elem, join)
        body = _collapse_ws(self._render_block(join.body, loop_env))
        sep = join.sep.strip()
        if sep == ",":
            each_body = f"{self.pp('COMMA_IF')}(i) {body}"
        else:
            sep_helper = self._add_helper("SEP", "", sep)
            each_body = f"{self.pp('IF')}(i, {sep_helper}, {self.pp('EMPTY')})() {body}"
        helper = self._add_helper("EACH", "r, d, i, e", each_body)
        return f"{self.pp('SEQ_FOR_EACH_I')}({helper}, ~, {seq.c_expr})"

    def _add_helper(self, kind: str, params: str, body: str) -> str:
        count = self._helper_counts.get(kind, 0) + 1
        self._helper_counts[kind] = count
        name = f"CURSEDPP_{self.macro.name}_{kind}{count}"
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


def emit_file(file: File, *, source_name: str, config: EmitConfig | None = None) -> str:
    config = config or EmitConfig()
    chunks: list[str] = [
        f"/* Generated by cursedpp from {source_name} — do not edit. */\n",
        "#pragma once\n",
        "\n",
        f"#include <{config.pp_include}>\n",
    ]
    for macro in file.macros:
        out = _MacroEmitter(macro, config, source_name).emit()
        chunks.append("\n")
        for helper in out.helpers:
            chunks.append(f"#define {helper.name}({helper.params}) {helper.body}\n")
        chunks.append(out.define)
    return "".join(chunks)


def compile_source(source: str, filename: str, *, config: EmitConfig | None = None) -> str:
    """Full pipeline: parse -> emit. Convenience for the CLI and tests."""
    file = parse_file(source, filename)
    return emit_file(
        file,
        source_name=filename.rsplit("/", 1)[-1],
        config=config,
    )
