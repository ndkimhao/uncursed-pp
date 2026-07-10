"""AST dataclasses for the cursedpp DSL."""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenT:
    """A bare preprocessor token argument."""


@dataclass(frozen=True)
class TupleT:
    names: tuple[str, ...]


@dataclass(frozen=True)
class SeqT:
    elem: TupleT | TokenT


Type = TokenT | TupleT | SeqT


# ── Expressions (inside {{...}} and conditions) ──────────────────────


@dataclass(frozen=True)
class VarRef:
    name: str


Expr = VarRef


# ── Body nodes ───────────────────────────────────────────────────────


@dataclass
class Text:
    value: str


@dataclass
class Interp:
    expr: Expr
    line: int = 0


@dataclass
class ForEach:
    unpack: tuple[str, ...] | None
    var: str | None
    iterable: str
    body: list[BodyNode] = field(default_factory=list)
    line: int = 0


BodyNode = Text | Interp | ForEach


# ── Top level ────────────────────────────────────────────────────────


@dataclass
class Param:
    name: str
    type: Type | None  # None = bare token param
    line: int = 0


@dataclass
class MacroDef:
    name: str
    params: list[Param]
    body: list[BodyNode]
    line: int = 0


@dataclass
class File:
    macros: list[MacroDef]
