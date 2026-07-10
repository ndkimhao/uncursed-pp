"""Parse .uncursed source into the AST.

Two-level strategy: a line-level pass recognizes macro headers, directive
lines, `@endmacro` terminators and raw body text; lark mini-grammars (grammar.lark)
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
    VariadicT,
    VarRef,
    VarTupleT,
)


class UncursedPpError(Exception):
    def __init__(self, message: str, filename: str, line: int, col: int | None = None):
        pos = f"{filename}:{line}" + (f":{col}" if col is not None else "")
        super().__init__(f"{pos}: {message}")
        self.filename = filename
        self.line = line
        self.col = col


_GRAMMAR = resources.files("uncursed_pp").joinpath("grammar.lark").read_text()
_LARK = Lark(
    _GRAMMAR,
    start=["signature", "for_line", "join_header", "let_line", "cond", "expr"],
    maybe_placeholders=True,
)

_INTERP_RE = re.compile(r"\{\{(.*?)\}\}")
# One source of truth for C literal tokenization (emitter and tests
# import it from there). Directive scanners embed it so string/char
# literals are opaque: '@if'/'@else'/'@end' inside one is literal text.
C_LITERAL_PATTERN = (
    r'(?:u8|[uUL])?R"(?P<_rawd>[^"()\\\s]*)\((?s:.*?)\)(?P=_rawd)"'
    r'|(?:u8|[uUL])?"(?:\\.|[^"\\])*"'
    r"|(?:u8|[uUL])?'(?:\\.|[^'\\])*'"
)

_INLINE_OPEN_RE = re.compile(C_LITERAL_PATTERN + r"|@(?P<kw>join|if)\s")
_INLINE_TOKEN_RE = re.compile(C_LITERAL_PATTERN + r"|@(?:join|if)\s|@else\b|@end\b")
_THEN_BOUNDARY_RE = re.compile(
    C_LITERAL_PATTERN + r"|@then\b|@(?:join|if)\s|@else\b|@end\b"
)
_STRAY_TOKEN_RE = re.compile(C_LITERAL_PATTERN + r"|@(?P<tok>else|end|for|let|then)\b")


def _search_directive(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """First match that is an @-token, skipping matched C literals."""
    for m in pattern.finditer(text):
        if m.group(0).startswith("@"):
            return m
    return None

class _Ast(Transformer[Any, Any]):
    def signature(self, items: list[Any]) -> tuple[str, list[Param]]:
        name, params = items
        return (str(name), params or [])

    def param_list(self, items: list[Any]) -> list[Param]:
        return list(items)

    def plain_param(self, items: list[Any]) -> Param:
        name, type_ = items
        return Param(name=str(name)[1:], type=type_)

    def defaulted_param(self, items: list[Any]) -> Param:
        name, default = items
        return Param(name=str(name)[1:], type=None, default=_clean_default(default))

    def named_param(self, items: list[Any]) -> Param:
        # a bare `named $X` (no `=`) is shorthand for an empty default
        name, default = items if len(items) == 2 else (items[0], None)
        return Param(name=str(name)[1:], type=None, default=_clean_default(default), named=True)

    def named_variadic_param(self, items: list[Any]) -> Param:
        name, default = items if len(items) == 2 else (items[0], None)
        return Param(
            name=str(name)[1:],
            type=None,
            default=_clean_default(default),
            named=True,
            variadic_value=True,
        )

    def required_named_param(self, items: list[Any]) -> Param:
        # no default: the keyword must appear at the call site
        return Param(name=str(items[0])[1:], type=None, default=None, named=True)

    def required_named_variadic_param(self, items: list[Any]) -> Param:
        return Param(
            name=str(items[0])[1:],
            type=None,
            default=None,
            named=True,
            variadic_value=True,
        )

    def seq_type(self, items: list[Any]) -> SeqT:
        return SeqT(items[0])

    def tuple_type(self, items: list[Any]) -> TupleT:
        return TupleT(items[0])

    def var_tuple_type(self, items: list[Any]) -> VarTupleT:
        return VarTupleT(items[0])

    def hybrid_tuple_type(self, items: list[Any]) -> VarTupleT:
        names, tail_elem = items
        return VarTupleT(tail_elem, names)

    def bare_var_tuple_type(self, _items: list[Any]) -> VarTupleT:
        return VarTupleT(TokenT())

    def token_type(self, _items: list[Any]) -> TokenT:
        return TokenT()

    def variadic_type(self, items: list[Any]) -> VariadicT:
        elem = items[0]
        if elem is None:
            return VariadicT()
        if not isinstance(elem, (TupleT, TokenT, VarTupleT)):
            raise ValueError("variadic elements must be token, tuple<...> or tuple<T...>")
        return VariadicT(elem)

    def name_list(self, items: list[Any]) -> tuple[str, ...]:
        return tuple(str(n)[1:] for n in items)

    def for_line(self, items: list[Any]) -> ForEach:
        target, iterable = items
        kind, value = target
        return ForEach(
            unpack=value if kind == "unpack" else None,
            var=value if kind == "var" else None,
            iterable=str(iterable)[1:],
        )

    def unpack_target(self, items: list[Any]) -> tuple[str, tuple[str, ...]]:
        return ("unpack", items[0])

    def var_target(self, items: list[Any]) -> tuple[str, str]:
        return ("var", str(items[0])[1:])

    def join_header(self, items: list[Any]) -> Join:
        iterable, var, sep = items
        return Join(
            var=str(var)[1:] if var else None,
            iterable=str(iterable)[1:],
            sep=_check_separator(_unquote(sep)),
        )

    def let_line(self, items: list[Any]) -> Let:
        name, expr = items
        return Let(name=str(name)[1:], expr=expr)

    def cond(self, items: list[Any]) -> Cond:
        lhs, op, value = items
        if op is None:
            if not isinstance(lhs, (IsParen, IsEmpty)):
                raise ValueError(
                    "@if condition must be a comparison, is_paren() or is_empty()"
                )
            return lhs
        return Cmp(lhs, str(op), int(value))

    def func_call(self, items: list[Any]) -> Expr:
        name, *args = items
        return _build_call(str(name), tuple(args))

    def var_postfix(self, items: list[Any]) -> Expr:
        base, *accessors = items
        return self._postfix(VarRef(str(base)[1:]), accessors)

    def literal_postfix(self, items: list[Any]) -> Expr:
        base, *accessors = items
        return self._postfix(Literal(str(base)), accessors)

    def _postfix(self, expr: Expr, accessors: list[Any]) -> Expr:
        for kind, value in accessors:
            expr = ElemAccess(expr, value)
        return expr

    def field_access(self, items: list[Any]) -> tuple[str, str]:
        return ("field", str(items[0])[1:])

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
    if name == "stringize":
        if len(args) != 1:
            raise ValueError("stringize() takes exactly one argument")
        return Stringize(args[0])
    if name == "len":
        if len(args) != 1:
            raise ValueError("len() takes exactly one argument")
        return Len(args[0])
    if name == "is_paren":
        if len(args) != 1:
            raise ValueError("is_paren() takes exactly one argument")
        return IsParen(args[0])
    if name == "is_empty":
        if len(args) != 1:
            raise ValueError("is_empty() takes exactly one argument")
        return IsEmpty(args[0])
    if name == "to_seq":
        if len(args) != 1:
            raise ValueError("to_seq() takes exactly one argument")
        return ToSeq(args[0])
    if name == "to_tuple":
        if len(args) != 1:
            raise ValueError("to_tuple() takes exactly one argument")
        return ToTuple(args[0])
    known = "concat, is_empty, is_paren, len, remove_parens, stringize, to_seq, to_tuple"
    raise ValueError(f"unknown function: {name}() (known: {known})")


def _clean_default(token: Any) -> str:
    value = "" if token is None else str(token).strip()
    # a default is spliced verbatim into generated defines: '$' refs and
    # {{...}} do not expand there, '#' is the stringize operator
    if "$" in value or "#" in value or "{{" in value:
        raise ValueError(
            f"default value {value!r} must be literal C tokens "
            "('$' references, '{{...}}', and '#' do not expand in defaults)"
        )
    return value


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\"}

# The separator is spliced verbatim into #define bodies: a newline splits
# the define, a trailing backslash line-continues into the NEXT define,
# '#' is the stringize operator, NUL is not a source character.
_SEP_FORBIDDEN = {"\n": "'\\n'", "\r": "'\\r'", "\0": "'\\0'", "\\": "'\\'", "#": "'#'"}


def _check_separator(sep: str) -> str:
    bad = [_SEP_FORBIDDEN[ch] for ch in dict.fromkeys(sep) if ch in _SEP_FORBIDDEN]
    if bad:
        raise ValueError(
            f"@join separator cannot contain {', '.join(bad)}: separators are "
            "spliced into generated #define bodies"
        )
    return sep


def _unquote(token: Any) -> str:
    """Translate escape sequences without corrupting non-ASCII text (the
    old encode/unicode_escape round-trip mojibake'd UTF-8)."""
    text = str(token)[1:-1]
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), text)


_TRANSFORM = _Ast()


def _parse_fragment(start: str, text: str, filename: str, line: int) -> Any:
    try:
        tree = _LARK.parse(text, start=start)
        return _TRANSFORM.transform(tree)
    except lark_exceptions.VisitError as exc:
        if isinstance(exc.orig_exc, ValueError):
            raise UncursedPpError(str(exc.orig_exc), filename, line) from exc
        raise
    except lark_exceptions.UnexpectedInput as exc:
        col = getattr(exc, "column", None)
        if col is not None and col < 1:
            col = None  # lark reports -1 at EOF; never print a lie
        raise UncursedPpError(f"syntax error: {exc.__class__.__name__}", filename, line, col) from exc


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
        if not token.startswith("@"):
            continue  # a C string/char literal: its @-tokens are text
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
    raise UncursedPpError("inline directive missing @end", filename, lineno)


def _parse_segments(text: str, lineno: int, filename: str) -> list[BodyNode]:
    nodes: list[BodyNode] = []
    m = _search_directive(_INLINE_OPEN_RE, text)
    if m:
        before, after_kw = text[: m.start()], text[m.end() :]
        nodes.extend(_parse_segments(before, lineno, filename))
        node: Join | If
        if m.group("kw") == "join":
            node, rest = _parse_inline_join(after_kw, lineno, filename)
        else:
            node, rest = _parse_inline_if(after_kw, lineno, filename)
        nodes.append(node)
        nodes.extend(_parse_segments(rest, lineno, filename))
        return nodes

    stray = _search_directive(_STRAY_TOKEN_RE, text)
    if stray:
        if stray.group("tok") in {"for", "let"}:
            raise UncursedPpError(
                f"{stray.group(0)} must start its own line", filename, lineno
            )
        raise UncursedPpError(f"stray {stray.group(0)} outside a directive", filename, lineno)

    pieces = _INTERP_RE.split(text)
    for i, piece in enumerate(pieces):
        if i % 2 == 0:
            if "{{" in piece:
                raise UncursedPpError(
                    "unclosed '{{' interpolation", filename, lineno
                )
            if piece:
                nodes.append(Text(piece))
        else:
            expr = _parse_fragment("expr", piece, filename, lineno)
            nodes.append(Interp(expr, line=lineno))
    return nodes


def _parse_inline_join(text: str, lineno: int, filename: str) -> tuple[Join, str]:
    colon = _find_colon_outside_quotes(text)
    if colon == -1:
        raise UncursedPpError("inline @join needs ': <body>@end'", filename, lineno)
    join = _parse_fragment("join_header", "join " + text[:colon], filename, lineno)
    join.line = lineno
    body_text = text[colon + 1 :].removeprefix(" ")
    body, _, rest = _scan_to_end(body_text, filename, lineno, capture_else=False)
    join.body = _parse_segments(body, lineno, filename)
    return join, rest


def _parse_inline_if(text: str, lineno: int, filename: str) -> tuple[If, str]:
    # '@then' marks where the condition ends and the then-text begins;
    # with the boundary explicit, the condition parses through the real
    # grammar (a condition contains no @-tokens, so the first one found
    # must be the @then)
    m = _search_directive(_THEN_BOUNDARY_RE, text)
    if m is None or m.group(0) != "@then":
        raise UncursedPpError(
            "inline @if needs '@then' between the condition and the "
            "then-text: @if <cond> @then <text> [@else <text>] @end",
            filename,
            lineno,
        )
    cond = _parse_fragment("cond", text[: m.start()].strip(), filename, lineno)
    then_text, else_text, rest = _scan_to_end(text[m.end() :], filename, lineno, capture_else=True)
    node = If(cond=cond, line=lineno)
    node.then = _parse_segments(then_text, lineno, filename)
    node.else_ = _parse_segments(else_text, lineno, filename) if else_text is not None else []
    return node, rest


def _find_colon_outside_quotes(text: str) -> int:
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string and ch == "\\":
            i += 2  # skip the escaped character, whatever it is
            continue
        if ch == '"':
            in_string = not in_string
        elif ch == ":" and not in_string:
            return i
        i += 1
    return -1


# ── line-level pass ──────────────────────────────────────────────────


@dataclass
class _Block:
    target: list[BodyNode]
    node: ForEach | Join | If | None
    kind: str  # root, for, join, if
    line: int = 0


_KNOWN_PRAGMAS = {"pp_prefix", "pp_include", "pp_include_dir", "helper_prefix", "runtime_name", "runtime_include", "arg_prefix", "include", "loop_chain", "loop_chain_limit"}


def parse_file(source: str, filename: str) -> File:
    macros: list[MacroDef] = []
    pragmas: dict[str, str] = {}
    extra_includes: list[str] = []
    comments: list[tuple[int, str]] = []
    directives: list[tuple[int, str]] = []
    lines = source.split("\n")
    i = 0
    pending: list[str] = []  # contiguous comment/spec lines, not yet placed

    def flush_standalone(upto: int | None = None) -> None:
        """Emit pending[:upto] as one standalone comment group."""
        count = len(pending) if upto is None else upto
        if count:
            comments.append((len(macros), "\n".join(pending[:count])))
        del pending[:count]

    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            flush_standalone()
            i += 1
            continue
        if stripped.startswith("#"):
            pending.append(lines[i])
            i += 1
            continue
        if stripped.startswith("@#"):
            # raw preprocessing-directive passthrough: '@#define X ...'
            # emits '#define X ...' VERBATIM at this source position
            # (trailing '\' continues onto the next line, like C)
            flush_standalone()
            text = stripped[1:]
            if text.strip() == "#":
                raise UncursedPpError(
                    "empty '@#' directive (expected e.g. '@#define NAME ...')",
                    filename,
                    i + 1,
                )
            parts = [text]
            while parts[-1].rstrip().endswith("\\"):
                i += 1
                if i >= len(lines) or not lines[i].strip():
                    raise UncursedPpError(
                        "'@#' line continuation with nothing to continue",
                        filename,
                        i,
                    )
                parts.append(lines[i])
            directives.append((len(macros), "\n".join(parts)))
            i += 1
            continue
        if stripped.startswith("@pragma "):
            flush_standalone()
            key, value = _parse_pragma(stripped, filename, i + 1)
            if key == "include":
                extra_includes.append(value)
            else:
                pragmas[key] = value
            i += 1
            continue
        if stripped.startswith("@macro ") or stripped == "@macro":
            # the trailing run of non-spec comment lines documents the macro
            # and joins its source block; anything before it (incl. #?/#=>
            # spec lines) stands alone
            split = len(pending)
            while split > 0 and not pending[split - 1].strip().startswith(("#?", "#=>")):
                split -= 1
            flush_standalone(split)
            attached = pending[:]
            pending.clear()
            start = i
            macro, i = _parse_macro(lines, i, filename)
            macro.source = "\n".join(attached + lines[start:i])
            macros.append(macro)
            continue
        raise UncursedPpError(f"unexpected line: {stripped!r}", filename, i + 1)
    flush_standalone()

    return File(
        macros=macros,
        pragmas=pragmas,
        extra_includes=extra_includes,
        comments=comments,
        directives=directives,
    )




# prefixes are pasted into generated identifiers - anything else in the
# value (spaces, quotes, punctuation) silently produces garbage defines
_IDENT_PRAGMAS = {"pp_prefix", "helper_prefix", "arg_prefix"}


def _parse_pragma(stripped: str, filename: str, lineno: int) -> tuple[str, str]:
    parts = stripped.split(None, 2)
    if len(parts) != 3:
        raise UncursedPpError("@pragma needs a key and a value", filename, lineno)
    _, key, value = parts
    if key not in _KNOWN_PRAGMAS:
        known = ", ".join(sorted(_KNOWN_PRAGMAS))
        raise UncursedPpError(f"unknown pragma {key!r} (known: {known})", filename, lineno)
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if key in _IDENT_PRAGMAS and not re.fullmatch(r"[A-Za-z_]\w*", value):
        raise UncursedPpError(
            f"@pragma {key} must be an identifier prefix "
            f"([A-Za-z_][A-Za-z0-9_]*), got {value!r}",
            filename,
            lineno,
        )
    if key == "runtime_name" and re.search(r"[\s\"']", value):
        raise UncursedPpError(
            f"@pragma runtime_name must be a plain filename, got {value!r}",
            filename,
            lineno,
        )
    if key == "loop_chain" and value not in {"on", "off"}:
        raise UncursedPpError(
            f"@pragma loop_chain takes 'on' or 'off', got {value!r}", filename, lineno
        )
    if key == "loop_chain_limit" and not (value.isdigit() and 1 <= int(value) <= 256):
        raise UncursedPpError(
            f"@pragma loop_chain_limit takes an integer in 1..256, got {value!r}",
            filename,
            lineno,
        )
    return key, value


def _directive_word(stripped: str) -> str | None:
    if not stripped.startswith("@") or len(stripped) == 1:
        return None
    return stripped[1:].split(None, 1)[0]


def _is_block_directive(stripped: str) -> bool:
    """A directive line opens/closes a block; inline @join/@if close on the same line."""
    word = _directive_word(stripped)
    if word == "join":
        # inline joins have ': <body>@end'; a block header has no colon
        # outside its separator string (which may itself contain '@end')
        return _find_colon_outside_quotes(stripped) == -1
    if word == "if":
        return "@end" not in stripped
    return word in {"for", "let", "else", "end"}


def _parse_macro(lines: list[str], start: int, filename: str) -> tuple[MacroDef, int]:
    header_line = start + 1  # 1-based
    # the parameter list may span lines: accumulate until parens balance
    # (signatures cannot contain nested parens - defaults exclude them)
    header = lines[start].strip()[len("@macro") :].strip()
    i = start + 1
    while "(" not in header or header.count("(") > header.count(")"):
        if i >= len(lines):
            raise UncursedPpError(
                "unclosed '@macro' parameter list", filename, header_line
            )
        if lines[i].strip().startswith("#"):
            i += 1  # comment lines inside the parameter list
            continue
        header = f"{header} {lines[i].strip()}"
        i += 1
    name, params = _parse_fragment("signature", header, filename, header_line)

    body: list[BodyNode] = []
    stack: list[_Block] = [_Block(body, None, "root")]

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        lineno = i + 1

        if stripped == "@endmacro":
            if len(stack) > 1:
                raise UncursedPpError(f"unclosed @{stack[-1].kind}", filename, lineno)
            return MacroDef(name=name, params=params, body=body, line=header_line), i + 1

        if stripped.startswith("#"):
            i += 1
            continue

        if stripped.startswith("@#"):
            raise UncursedPpError(
                "'@#' directives are only allowed at the top level (a "
                "generated #define cannot contain another directive)",
                filename,
                lineno,
            )

        if _is_block_directive(stripped):
            _handle_directive(stripped[1:], stack, filename, lineno)
            i += 1
            continue

        word = _directive_word(stripped)
        if word == "pragma":
            raise UncursedPpError(
                "@pragma is only allowed at the top level, before any macro",
                filename,
                lineno,
            )
        if word is not None and word not in {"join", "if"} and not _search_directive(_INLINE_OPEN_RE, raw):
            raise UncursedPpError(
                f"unknown directive: @{word} (known: for, join, if, else, "
                "end, let, pragma)",
                filename,
                lineno,
            )

        segments = _parse_segments(raw, lineno, filename)
        if segments and isinstance(segments[-1], Text):
            segments[-1].value += "\n"
        else:
            segments.append(Text("\n"))
        stack[-1].target.extend(segments)
        i += 1

    if len(stack) > 1:
        raise UncursedPpError(f"unclosed @{stack[-1].kind}", filename, stack[-1].line)
    raise UncursedPpError(f"missing '@endmacro' for macro {name}", filename, header_line)


_LET_RE = re.compile(r"let\s+\$([A-Za-z_]\w*)\s*:=\s*(.*)$")


def _parse_let(directive: str, filename: str, lineno: int) -> Let:
    m = _LET_RE.match(directive)
    if not m:
        raise UncursedPpError("@let needs '$name := value'", filename, lineno)
    name, rhs = m.group(1), m.group(2).strip()
    if _INLINE_OPEN_RE.match(rhs):
        nodes = _parse_segments(rhs, lineno, filename)
        nodes = [n for n in nodes if not (isinstance(n, Text) and not n.value.strip())]
        if len(nodes) != 1 or not isinstance(nodes[0], (Join, If)):
            raise UncursedPpError(
                "@let value must be a single expression or one inline @join/@if",
                filename,
                lineno,
            )
        return Let(name=name, expr=nodes[0], line=lineno)
    expr = _parse_fragment("expr", rhs, filename, lineno)
    return Let(name=name, expr=expr, line=lineno)


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
        # a block @if owns the whole rest of the line, parsed with the
        # real grammar; a trailing '@then' is optional (mandatory only in
        # the inline form, where it marks the condition's end)
        cond_text = directive[len("if ") :].strip()
        if cond_text.endswith("@then"):
            cond_text = cond_text[: -len("@then")].strip()
        cond = _parse_fragment("cond", cond_text, filename, lineno)
        node = If(cond=cond, line=lineno)
        stack[-1].target.append(node)
        stack.append(_Block(node.then, node, "if", lineno))
    elif word == "else":
        if directive.strip() != "else":
            raise UncursedPpError(
                "unexpected text after @else (for else-if, nest an @if "
                "inside the @else branch)",
                filename,
                lineno,
            )
        block = stack[-1]
        if block.kind != "if" or not isinstance(block.node, If):
            raise UncursedPpError("@else without matching @if", filename, lineno)
        if block.target is block.node.else_:
            raise UncursedPpError("duplicate @else", filename, lineno)
        block.target = block.node.else_
    elif word == "let":
        let = _parse_let(directive, filename, lineno)
        stack[-1].target.append(let)
    elif word == "end":
        if directive.strip() != "end":
            raise UncursedPpError("unexpected text after @end", filename, lineno)
        if len(stack) == 1:
            raise UncursedPpError("@end without matching @for/@join/@if", filename, lineno)
        stack.pop()
    else:  # pragma: no cover - guarded by _is_block_directive
        raise UncursedPpError(f"unknown directive: @{directive}", filename, lineno)
