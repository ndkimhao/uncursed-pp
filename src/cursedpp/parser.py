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
    File,
    ForEach,
    Interp,
    MacroDef,
    Param,
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
_LARK = Lark(_GRAMMAR, start=["signature", "for_line", "expr"], maybe_placeholders=True)

_INTERP_RE = re.compile(r"\{\{(.*?)\}\}")


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

    def var_ref(self, items):
        return VarRef(str(items[0]))


_TRANSFORM = _Ast()


def _parse_fragment(start: str, text: str, filename: str, line: int):
    try:
        tree = _LARK.parse(text, start=start)
    except lark_exceptions.UnexpectedInput as exc:
        col = getattr(exc, "column", None)
        raise CursedppError(f"syntax error: {exc.__class__.__name__}", filename, line, col) from exc
    return _TRANSFORM.transform(tree)


def _parse_body_line(line: str, lineno: int, filename: str) -> list[Text | Interp]:
    """Split one raw body line into Text / Interp segments (with trailing newline)."""
    nodes: list[Text | Interp] = []
    pieces = _INTERP_RE.split(line)
    # pieces alternates: text, expr, text, expr, ..., text
    for i, piece in enumerate(pieces):
        if i % 2 == 0:
            if piece:
                nodes.append(Text(piece))
        else:
            expr = _parse_fragment("expr", piece, filename, lineno)
            nodes.append(Interp(expr, line=lineno))
    if nodes and isinstance(nodes[-1], Text):
        nodes[-1].value += "\n"
    else:
        nodes.append(Text("\n"))
    return nodes


def parse_file(source: str, filename: str) -> File:
    macros: list[MacroDef] = []
    lines = source.split("\n")
    i = 0

    def current(idx: int) -> str:
        return lines[idx]

    while i < len(lines):
        raw = current(i)
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if stripped.startswith("macro "):
            macro, i = _parse_macro(lines, i, filename)
            macros.append(macro)
            continue
        raise CursedppError(f"unexpected line: {stripped!r}", filename, i + 1)

    return File(macros=macros)


def _parse_macro(lines: list[str], start: int, filename: str) -> tuple[MacroDef, int]:
    header_line = start + 1  # 1-based
    header = lines[start].strip()[len("macro ") :]
    name, params = _parse_fragment("signature", header, filename, header_line)

    body: list = []
    stack: list[list] = [body]  # innermost block last
    open_loops: list[int] = []  # line numbers of unclosed @for

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

        if stripped.startswith("@"):
            directive = stripped[1:]
            if directive.split(None, 1)[0] == "for":
                loop = _parse_fragment("for_line", directive, filename, lineno)
                loop.line = lineno
                stack[-1].append(loop)
                stack.append(loop.body)
                open_loops.append(lineno)
            elif directive == "end":
                if len(stack) == 1:
                    raise CursedppError("@end without matching @for", filename, lineno)
                stack.pop()
                open_loops.pop()
            else:
                raise CursedppError(f"unknown directive: @{directive}", filename, lineno)
            i += 1
            continue

        stack[-1].extend(_parse_body_line(raw, lineno, filename))
        i += 1

    if open_loops:
        raise CursedppError("unclosed @for", filename, open_loops[-1])
    raise CursedppError(f"missing 'end' for macro {name}", filename, header_line)
