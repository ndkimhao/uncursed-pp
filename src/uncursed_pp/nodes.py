"""AST dataclasses for the uncursed-pp DSL."""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenT:
    """A bare preprocessor token argument."""


@dataclass(frozen=True)
class TupleT:
    names: tuple[str, ...]
    # aligned with names when any field is optional (else empty):
    # defaults[i] is the fill value for an omitted `$f = value` field,
    # None for required fields and `?` fields
    defaults: tuple[str | None, ...] = ()
    # indices of `?` fields: truly absent when omitted (an omitted slot
    # normalizes to zero tokens; query presence with has($t.$f))
    maybe: tuple[int, ...] = ()
    # tuple_or_token<...>: call sites may pass a BARE token for the
    # single required field (opt-in: costs a per-element paren probe)
    or_token: bool = False

    @property
    def required(self) -> int:
        """Number of leading fields the call site must provide."""
        if not self.defaults:
            return len(self.names)
        optional = {i for i, d in enumerate(self.defaults) if d is not None}
        optional |= set(self.maybe)
        return min(optional, default=len(self.names))

    @property
    def has_optional(self) -> bool:
        return bool(self.defaults) and self.required < len(self.names)


@dataclass(frozen=True)
class VarTupleT:
    """Unbounded tuple `tuple<T...>` (bare `tuple` = `tuple<token...>`):
    a parenthesized comma list with a variable element count, all of one
    element type. Loops/len are emptiness-gated so `()` means zero
    elements; capped at 64 elements (BOOST_PP_VARIADIC_SIZE).

    Non-empty `names` makes it a HYBRID `tuple<n1, .., nk, T...>`: fixed
    named leading fields, then the unbounded tail. Named access reads the
    head; iteration/len/is_empty/[i] are all TAIL-scoped (they must agree
    with each other)."""

    elem: "Type" = TokenT()
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeqT:
    elem: "Type"
    # seq_or_token<T>: call sites may pass a BARE token, promoted to a
    # single-element seq (opt-in: costs a per-value paren probe)
    or_token: bool = False


@dataclass(frozen=True)
class VariadicT:
    """Trailing ... parameter; body sees it as a seq of `elem` values."""

    elem: "Type" = TokenT()


Type = TokenT | TupleT | VarTupleT | SeqT | VariadicT


# ── Expressions (inside {{...}} and conditions) ──────────────────────


@dataclass(frozen=True)
class VarRef:
    """A $-prefixed variable reference; `name` is stored without the $."""

    name: str


@dataclass(frozen=True)
class Literal:
    """A bare name in an expression: always a literal token, never a
    variable (only valid where literals are allowed, e.g. concat args)."""

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


@dataclass(frozen=True)
class IsEmpty:
    arg: "Expr"


@dataclass(frozen=True)
class Has:
    """Presence probe for a `?` tuple field: has($t.$f) is 1 when the
    call site provided the field, 0 when it is absent."""

    arg: "Expr"


@dataclass(frozen=True)
class ToSeq:
    """Explicit shape conversion: unbounded-tuple value -> seq (hybrids
    convert their tail); identity on values already seq-typed."""

    arg: "Expr"


@dataclass(frozen=True)
class ToTuple:
    """Explicit shape conversion: seq value -> unbounded tuple; identity
    on values already tuple-typed."""

    arg: "Expr"


Expr = (
    VarRef | Literal | ElemAccess | Concat | RemoveParens | Stringize
    | Len | IsParen | IsEmpty | Has | ToSeq | ToTuple
)


# ── Conditions ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cmp:
    lhs: Expr
    op: str  # ==, !=, <, >, <=, >=
    value: int


Cond = Cmp | IsParen | IsEmpty | Has


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
    source: str = ""  # original .uncursed text incl. attached comments


@dataclass
class File:
    macros: list[MacroDef]
    pragmas: dict[str, str] = field(default_factory=dict)
    extra_includes: list[str] = field(default_factory=list)
    # standalone top-level comment groups (incl. #?/#=> spec lines),
    # as (number of macros preceding the group, raw text) - reproduced
    # in the generated header at the same position
    comments: list[tuple[int, str]] = field(default_factory=list)
    # raw '@#' preprocessing directives, same anchoring as comments;
    # emitted verbatim (already '#'-prefixed, continuations included)
    directives: list[tuple[int, str]] = field(default_factory=list)
