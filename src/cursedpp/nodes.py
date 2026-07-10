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


@dataclass(frozen=True)
class VariadicT:
    """Trailing ... parameter; body sees it as a seq of `elem` values."""

    elem: TupleT | TokenT = TokenT()


Type = TokenT | TupleT | SeqT | VariadicT


# ── Expressions (inside {{...}} and conditions) ──────────────────────


@dataclass(frozen=True)
class VarRef:
    name: str


@dataclass(frozen=True)
class ElemAccess:
    base: "Expr"
    accessor: str | int  # str = tuple field name, int = seq index


@dataclass(frozen=True)
class Concat:
    args: tuple["Expr", ...]


@dataclass(frozen=True)
class RemoveParens:
    arg: "Expr"


@dataclass(frozen=True)
class Stringize:
    arg: "Expr"


@dataclass(frozen=True)
class Len:
    arg: "Expr"


@dataclass(frozen=True)
class IsParen:
    arg: "Expr"


Expr = VarRef | ElemAccess | Concat | RemoveParens | Stringize | Len | IsParen


# ── Conditions ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cmp:
    lhs: Expr
    op: str  # ==, !=, <, >, <=, >=
    value: int


Cond = Cmp | IsParen


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


@dataclass
class Join:
    var: str | None
    iterable: str
    sep: str
    body: list[BodyNode] = field(default_factory=list)
    line: int = 0


@dataclass
class Let:
    name: str
    expr: "Expr | Join | If"  # inline @join/@if render to a reusable value
    line: int = 0


@dataclass
class If:
    cond: Cond
    then: list[BodyNode] = field(default_factory=list)
    else_: list[BodyNode] = field(default_factory=list)
    line: int = 0


BodyNode = Text | Interp | ForEach | Join | Let | If


# ── Top level ────────────────────────────────────────────────────────


@dataclass
class Param:
    name: str
    type: Type | None  # None = bare token param
    default: str | None = None  # None = required; "" = defaults to empty
    named: bool = False  # passed as NAME(value) at the call site
    variadic_value: bool = False  # named value may contain bare commas
    line: int = 0


@dataclass
class MacroDef:
    name: str
    params: list[Param]
    body: list[BodyNode]
    line: int = 0
    source: str = ""  # original .cursed text incl. attached comments


@dataclass
class File:
    macros: list[MacroDef]
    pragmas: dict[str, str] = field(default_factory=dict)
    extra_includes: list[str] = field(default_factory=list)
