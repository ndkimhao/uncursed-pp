"""Parse .cursed source into the AST.

Two-level strategy: a line-level pass recognizes macro headers, directive
lines, `end` terminators and raw body text; lark mini-grammars (grammar.lark)
parse the structured fragments (signatures, directives, {{expr}} contents).
Inline directives (@join / @if inside a body line) are handled by a small
depth-aware scanner so they can nest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from lark import Lark, Transformer, exceptions as lark_exceptions

from .nodes import (
    BodyNode,
    Cmp,
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
    VarRef,
)


class CursedppError(Exception):
    def __init__(self, message: str, filename: str, line: int, col: int | None = None):
        pos = f"{filename}:{line}" + (f":{col}" if col is not None else "")
        super().__init__(f"{pos}: {message}")
        self.filename = filename
        self.line = line
        self.col = col


_GRAMMAR = resources.files("cursedpp").joinpath("grammar.lark").read_text()
_LARK = Lark(
    _GRAMMAR,
    start=["signature", "for_line", "join_header", "let_line", "cond", "expr"],
    maybe_placeholders=True,
)

_INTERP_RE = re.compile(r"\{\{(.*?)\}\}")
_INLINE_OPEN_RE = re.compile(r"@(join|if)\s")
_INLINE_TOKEN_RE = re.compile(r"@(join|if)\s|@else\b|@end\b")
_STRAY_TOKEN_RE = re.compile(r"@(else|end)\b")

_CMP = r"(?:==|!=|<=|>=|<|>)"
_COND_PATTERNS = [
    re.compile(rf"len\(\s*\w+\s*\)\s*{_CMP}\s*\d+"),
    re.compile(rf"is_paren\((?:[^()]|\([^()]*\))*\)(?:\s*{_CMP}\s*\d+)?"),
    re.compile(rf"[A-Za-z_][\w.\[\]]*\s*{_CMP}\s*\d+"),
]


class _Ast(Transformer[Any, Any]):
    def signature(self, items: list[Any]) -> tuple[str, list[Param]]:
        name, params = items
        return (str(name), params or [])

    def param_list(self, items: list[Any]) -> list[Param]:
        return list(items)

    def plain_param(self, items: list[Any]) -> Param:
        name, type_ = items
        return Param(name=str(name), type=type_)

    def defaulted_param(self, items: list[Any]) -> Param:
        name, default = items
        return Param(name=str(name), type=None, default=_clean_default(default))

    def named_param(self, items: list[Any]) -> Param:
        name, default = items
        return Param(name=str(name), type=None, default=_clean_default(default), named=True)

    def seq_type(self, items: list[Any]) -> SeqT:
        return SeqT(items[0])

    def tuple_type(self, items: list[Any]) -> TupleT:
        return TupleT(items[0])

    def token_type(self, _items: list[Any]) -> TokenT:
        return TokenT()

    def name_list(self, items: list[Any]) -> tuple[str, ...]:
        return tuple(str(n) for n in items)

    def for_line(self, items: list[Any]) -> ForEach:
        target, iterable = items
        kind, value = target
        return ForEach(
            unpack=value if kind == "unpack" else None,
            var=value if kind == "var" else None,
            iterable=str(iterable),
        )

    def unpack_target(self, items: list[Any]) -> tuple[str, tuple[str, ...]]:
        return ("unpack", items[0])

    def var_target(self, items: list[Any]) -> tuple[str, str]:
        return ("var", str(items[0]))

    def join_header(self, items: list[Any]) -> Join:
        iterable, var, sep = items
        return Join(var=str(var) if var else None, iterable=str(iterable), sep=_unquote(sep))

    def let_line(self, items: list[Any]) -> Let:
        name, expr = items
        return Let(name=str(name), expr=expr)

    def cond(self, items: list[Any]) -> Cond:
        lhs, op, value = items
        if op is None:
            if not isinstance(lhs, IsParen):
                raise ValueError("@if condition must be a comparison or is_paren()")
            return lhs
        return Cmp(lhs, str(op), int(value))

    def func_call(self, items: list[Any]) -> Expr:
        name, *args = items
        return _build_call(str(name), tuple(args))

    def postfix(self, items: list[Any]) -> Expr:
        base, *accessors = items
        expr: Expr = VarRef(str(base))
        for kind, value in accessors:
            expr = ElemAccess(expr, value)
        return expr

    def field_access(self, items: list[Any]) -> tuple[str, str]:
        return ("field", str(items[0]))

    def index_access(self, items: list[Any]) -> tuple[str, int]:
        return ("index", int(items[0]))

    def expr(self, items: list[Any]) -> Expr:
        return items[0]  # type: ignore[no-any-return]


def _build_call(name: str, args: tuple[Expr, ...]) -> Expr:
    if name == "concat":
        if len(args) < 2:
            raise ValueError("concat() needs at least two arguments")
        return Concat(args)
    if name == "remove_parens":
        if len(args) != 1:
            raise ValueError("remove_parens() takes exactly one argument")
        return RemoveParens(args[0])
    if name == "len":
        if len(args) != 1:
            raise ValueError("len() takes exactly one argument")
        return Len(args[0])
    if name == "is_paren":
        if len(args) != 1:
            raise ValueError("is_paren() takes exactly one argument")
        return IsParen(args[0])
    known = "concat, is_paren, len, remove_parens"
    raise ValueError(f"unknown function: {name}() (known: {known})")


def _clean_default(token: Any) -> str:
    return "" if token is None else str(token).strip()


def _unquote(token: Any) -> str:
    text = str(token)
    return text[1:-1].encode().decode("unicode_escape")


_TRANSFORM = _Ast()


def _parse_fragment(start: str, text: str, filename: str, line: int) -> Any:
    try:
        tree = _LARK.parse(text, start=start)
        return _TRANSFORM.transform(tree)
    except lark_exceptions.VisitError as exc:
        if isinstance(exc.orig_exc, ValueError):
            raise CursedppError(str(exc.orig_exc), filename, line) from exc
        raise
    except lark_exceptions.UnexpectedInput as exc:
        col = getattr(exc, "column", None)
        raise CursedppError(f"syntax error: {exc.__class__.__name__}", filename, line, col) from exc


# ── inline scanning ──────────────────────────────────────────────────


def _scan_to_end(
    text: str, filename: str, lineno: int, *, capture_else: bool
) -> tuple[str, str | None, str]:
    """Scan to the @end matching depth 0; return (then_text, else_text, rest).

    Nested inline @join/@if openers increase depth so their @ends are skipped.
    """
    depth = 0
    else_at: tuple[int, int] | None = None
    for m in _INLINE_TOKEN_RE.finditer(text):
        token = m.group(0)
        if token.startswith("@join") or token.startswith("@if"):
            depth += 1
        elif token == "@else":
            if depth == 0 and capture_else and else_at is None:
                else_at = (m.start(), m.end())
        else:  # @end
            if depth == 0:
                if else_at is not None:
                    return text[: else_at[0]], text[else_at[1] : m.start()], text[m.end() :]
                return text[: m.start()], None, text[m.end() :]
            depth -= 1
    raise CursedppError("inline directive missing @end", filename, lineno)


def _match_cond(text: str, filename: str, lineno: int) -> tuple[Cond, str]:
    for pattern in _COND_PATTERNS:
        m = pattern.match(text)
        if m:
            cond = _parse_fragment("cond", m.group(0), filename, lineno)
            return cond, text[m.end() :]
    raise CursedppError("cannot parse @if condition", filename, lineno)


def _parse_segments(text: str, lineno: int, filename: str) -> list[BodyNode]:
    nodes: list[BodyNode] = []
    m = _INLINE_OPEN_RE.search(text)
    if m:
        before, after_kw = text[: m.start()], text[m.end() :]
        nodes.extend(_parse_segments(before, lineno, filename))
        node: Join | If
        if m.group(1) == "join":
            node, rest = _parse_inline_join(after_kw, lineno, filename)
        else:
            node, rest = _parse_inline_if(after_kw, lineno, filename)
        nodes.append(node)
        nodes.extend(_parse_segments(rest, lineno, filename))
        return nodes

    stray = _STRAY_TOKEN_RE.search(text)
    if stray:
        raise CursedppError(f"stray {stray.group(0)} outside a directive", filename, lineno)

    pieces = _INTERP_RE.split(text)
    for i, piece in enumerate(pieces):
        if i % 2 == 0:
            if piece:
                nodes.append(Text(piece))
        else:
            expr = _parse_fragment("expr", piece, filename, lineno)
            nodes.append(Interp(expr, line=lineno))
    return nodes


def _parse_inline_join(text: str, lineno: int, filename: str) -> tuple[Join, str]:
    colon = _find_colon_outside_quotes(text)
    if colon == -1:
        raise CursedppError("inline @join needs ': <body>@end'", filename, lineno)
    join = _parse_fragment("join_header", "join " + text[:colon], filename, lineno)
    join.line = lineno
    body_text = text[colon + 1 :].removeprefix(" ")
    body, _, rest = _scan_to_end(body_text, filename, lineno, capture_else=False)
    join.body = _parse_segments(body, lineno, filename)
    return join, rest


def _parse_inline_if(text: str, lineno: int, filename: str) -> tuple[If, str]:
    cond, after_cond = _match_cond(text, filename, lineno)
    then_text, else_text, rest = _scan_to_end(after_cond, filename, lineno, capture_else=True)
    node = If(cond=cond, line=lineno)
    node.then = _parse_segments(then_text, lineno, filename)
    node.else_ = _parse_segments(else_text, lineno, filename) if else_text is not None else []
    return node, rest


def _find_colon_outside_quotes(text: str) -> int:
    in_string = False
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_string = not in_string
        elif ch == ":" and not in_string:
            return i
    return -1


# ── line-level pass ──────────────────────────────────────────────────


@dataclass
class _Block:
    target: list[BodyNode]
    node: ForEach | Join | If | None
    kind: str  # root, for, join, if
    line: int = 0


def parse_file(source: str, filename: str) -> File:
    macros: list[MacroDef] = []
    lines = source.split("\n")
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if stripped.startswith("macro "):
            macro, i = _parse_macro(lines, i, filename)
            macros.append(macro)
            continue
        raise CursedppError(f"unexpected line: {stripped!r}", filename, i + 1)

    return File(macros=macros)


def _directive_word(stripped: str) -> str | None:
    if not stripped.startswith("@") or len(stripped) == 1:
        return None
    return stripped[1:].split(None, 1)[0]


def _is_block_directive(stripped: str) -> bool:
    """A directive line opens/closes a block; inline @join/@if close on the same line."""
    word = _directive_word(stripped)
    if word in {"join", "if"}:
        return "@end" not in stripped
    return word in {"for", "let", "else", "end"}


def _parse_macro(lines: list[str], start: int, filename: str) -> tuple[MacroDef, int]:
    header_line = start + 1  # 1-based
    header = lines[start].strip()[len("macro ") :]
    name, params = _parse_fragment("signature", header, filename, header_line)

    body: list[BodyNode] = []
    stack: list[_Block] = [_Block(body, None, "root")]

    i = start + 1
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        lineno = i + 1

        if stripped == "end" and len(stack) == 1:
            return MacroDef(name=name, params=params, body=body, line=header_line), i + 1

        if stripped.startswith("#"):
            i += 1
            continue

        if _is_block_directive(stripped):
            _handle_directive(stripped[1:], stack, filename, lineno)
            i += 1
            continue

        segments = _parse_segments(raw, lineno, filename)
        if segments and isinstance(segments[-1], Text):
            segments[-1].value += "\n"
        else:
            segments.append(Text("\n"))
        stack[-1].target.extend(segments)
        i += 1

    if len(stack) > 1:
        raise CursedppError(f"unclosed @{stack[-1].kind}", filename, stack[-1].line)
    raise CursedppError(f"missing 'end' for macro {name}", filename, header_line)


def _handle_directive(directive: str, stack: list[_Block], filename: str, lineno: int) -> None:
    word = directive.split(None, 1)[0]
    if word == "for":
        loop = _parse_fragment("for_line", directive, filename, lineno)
        loop.line = lineno
        stack[-1].target.append(loop)
        stack.append(_Block(loop.body, loop, "for", lineno))
    elif word == "join":
        join = _parse_fragment("join_header", directive, filename, lineno)
        join.line = lineno
        stack[-1].target.append(join)
        stack.append(_Block(join.body, join, "join", lineno))
    elif word == "if":
        cond, leftover = _match_cond(directive[len("if ") :].strip(), filename, lineno)
        if leftover.strip():
            raise CursedppError("unexpected text after @if condition", filename, lineno)
        node = If(cond=cond, line=lineno)
        stack[-1].target.append(node)
        stack.append(_Block(node.then, node, "if", lineno))
    elif word == "else":
        block = stack[-1]
        if block.kind != "if" or not isinstance(block.node, If):
            raise CursedppError("@else without matching @if", filename, lineno)
        if block.target is block.node.else_:
            raise CursedppError("duplicate @else", filename, lineno)
        block.target = block.node.else_
    elif word == "let":
        let = _parse_fragment("let_line", directive, filename, lineno)
        let.line = lineno
        stack[-1].target.append(let)
    elif directive == "end":
        if len(stack) == 1:
            raise CursedppError("@end without matching @for/@join/@if", filename, lineno)
        stack.pop()
    else:  # pragma: no cover - guarded by _is_block_directive
        raise CursedppError(f"unknown directive: @{directive}", filename, lineno)
