"""Parse .cursed source into the AST.

Two-level strategy: a line-level pass recognizes macro headers, directive
lines, `end` terminators and raw body text; lark mini-grammars (grammar.lark)
parse the structured fragments (signatures, directives, {{expr}} contents).
"""

from __future__ import annotations

import re
from importlib import resources

from lark import Lark, Transformer, exceptions as lark_exceptions

from .nodes import (
    Concat,
    ElemAccess,
    File,
    ForEach,
    Interp,
    Join,
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
    start=["signature", "for_line", "join_header", "let_line", "expr"],
    maybe_placeholders=True,
)

_INTERP_RE = re.compile(r"\{\{(.*?)\}\}")
_FUNCTIONS = {"concat", "remove_parens"}


class _Ast(Transformer):
    def signature(self, items):
        name, params = items
        return (str(name), params or [])

    def param_list(self, items):
        return list(items)

    def param(self, items):
        name, type_ = items
        return Param(name=str(name), type=type_)

    def seq_type(self, items):
        return SeqT(items[0])

    def tuple_type(self, items):
        return TupleT(items[0])

    def token_type(self, _items):
        return TokenT()

    def name_list(self, items):
        return tuple(str(n) for n in items)

    def for_line(self, items):
        target, iterable = items
        kind, value = target
        return ForEach(
            unpack=value if kind == "unpack" else None,
            var=value if kind == "var" else None,
            iterable=str(iterable),
        )

    def unpack_target(self, items):
        return ("unpack", items[0])

    def var_target(self, items):
        return ("var", str(items[0]))

    def join_header(self, items):
        iterable, var, sep = items
        return Join(var=str(var) if var else None, iterable=str(iterable), sep=_unquote(sep))

    def let_line(self, items):
        name, expr = items
        return Let(name=str(name), expr=expr)

    def func_call(self, items):
        name, *args = items
        return _build_call(str(name), tuple(args))

    def postfix(self, items):
        base, *accessors = items
        expr = VarRef(str(base))
        for kind, value in accessors:
            expr = ElemAccess(expr, value)
        return expr

    def field_access(self, items):
        return ("field", str(items[0]))

    def index_access(self, items):
        return ("index", int(items[0]))

    def expr(self, items):
        return items[0]


def _build_call(name: str, args: tuple):
    if name == "concat":
        if len(args) < 2:
            raise ValueError("concat() needs at least two arguments")
        return Concat(args)
    if name == "remove_parens":
        if len(args) != 1:
            raise ValueError("remove_parens() takes exactly one argument")
        return RemoveParens(args[0])
    raise ValueError(f"unknown function: {name}() (known: {', '.join(sorted(_FUNCTIONS))})")


def _unquote(token) -> str:
    text = str(token)
    return text[1:-1].encode().decode("unicode_escape")


_TRANSFORM = _Ast()


def _parse_fragment(start: str, text: str, filename: str, line: int):
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


def _parse_segments(text: str, lineno: int, filename: str) -> list:
    """Split raw body text into Text / Interp / inline-Join segments."""
    nodes: list = []
    join_at = text.find("@join ")
    if join_at != -1:
        before, join_node, after = _split_inline_join(text, join_at, lineno, filename)
        nodes.extend(_parse_segments(before, lineno, filename) if before else [])
        nodes.append(join_node)
        nodes.extend(_parse_segments(after, lineno, filename) if after else [])
        return nodes

    pieces = _INTERP_RE.split(text)
    for i, piece in enumerate(pieces):
        if i % 2 == 0:
            if piece:
                nodes.append(Text(piece))
        else:
            expr = _parse_fragment("expr", piece, filename, lineno)
            nodes.append(Interp(expr, line=lineno))
    return nodes


def _split_inline_join(text: str, join_at: int, lineno: int, filename: str):
    """Split `...@join <header>: <body>@end...` into (before, Join, after)."""
    colon = _find_colon_outside_quotes(text, join_at)
    if colon == -1:
        raise CursedppError("inline @join needs ': <body>@end'", filename, lineno)
    header = text[join_at + 1 : colon]  # includes the word 'join'
    join_node = _parse_fragment("join_header", header, filename, lineno)
    join_node.line = lineno

    body_text = text[colon + 1 :]
    if body_text.startswith(" "):
        body_text = body_text[1:]
    end_at = body_text.find("@end")
    if end_at == -1:
        raise CursedppError("inline @join missing @end", filename, lineno)
    join_node.body = _parse_segments(body_text[:end_at], lineno, filename)
    return text[:join_at], join_node, body_text[end_at + len("@end") :]


def _find_colon_outside_quotes(text: str, start: int) -> int:
    in_string = False
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '"' and text[i - 1] != "\\":
            in_string = not in_string
        elif ch == ":" and not in_string:
            return i
    return -1


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


def _is_block_directive(stripped: str) -> bool:
    """A directive line opens/closes a block; inline @join lines are body text."""
    if not stripped.startswith("@"):
        return False
    word = stripped[1:].split(None, 1)[0] if len(stripped) > 1 else ""
    if word == "join":
        return "@end" not in stripped  # inline joins close on the same line
    return word in {"for", "let", "end"}


def _parse_macro(lines: list[str], start: int, filename: str) -> tuple[MacroDef, int]:
    header_line = start + 1  # 1-based
    header = lines[start].strip()[len("macro ") :]
    name, params = _parse_fragment("signature", header, filename, header_line)

    body: list = []
    stack: list[list] = [body]  # innermost block last
    open_blocks: list[int] = []  # line numbers of unclosed @for/@join

    i = start + 1
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        lineno = i + 1

        if stripped == "end" and len(stack) == 1:
            macro = MacroDef(name=name, params=params, body=body, line=header_line)
            return macro, i + 1

        if stripped.startswith("#"):
            i += 1
            continue

        if _is_block_directive(stripped):
            directive = stripped[1:]
            word = directive.split(None, 1)[0]
            if word == "for":
                loop = _parse_fragment("for_line", directive, filename, lineno)
                loop.line = lineno
                stack[-1].append(loop)
                stack.append(loop.body)
                open_blocks.append(lineno)
            elif word == "join":
                join = _parse_fragment("join_header", directive, filename, lineno)
                join.line = lineno
                stack[-1].append(join)
                stack.append(join.body)
                open_blocks.append(lineno)
            elif word == "let":
                let = _parse_fragment("let_line", directive, filename, lineno)
                let.line = lineno
                stack[-1].append(let)
            elif directive == "end":
                if len(stack) == 1:
                    raise CursedppError("@end without matching @for/@join", filename, lineno)
                stack.pop()
                open_blocks.pop()
            else:
                raise CursedppError(f"unknown directive: @{directive}", filename, lineno)
            i += 1
            continue

        if stripped.startswith("@") and not stripped[1:].split(None, 1)[0] == "join":
            raise CursedppError(f"unknown directive: @{stripped[1:]}", filename, lineno)

        segments = _parse_segments(raw, lineno, filename)
        if segments and isinstance(segments[-1], Text):
            segments[-1].value += "\n"
        else:
            segments.append(Text("\n"))
        stack[-1].extend(segments)
        i += 1

    if open_blocks:
        raise CursedppError("unclosed @for/@join", filename, open_blocks[-1])
    raise CursedppError(f"missing 'end' for macro {name}", filename, header_line)
