"""Unbounded tuples: `tuple` / `tuple<T...>` — variable element count,
one element type. AST shape and error paths only; codegen shape lives in
the goldens, runtime behavior in the #? specs.
"""

import pytest

from uncursed_pp.emitter import compile_source
from uncursed_pp.nodes import (
    If,
    IsEmpty,
    SeqT,
    TokenT,
    TupleT,
    VariadicT,
    VarTupleT,
)
from uncursed_pp.parser import UncursedPpError, parse_file


def _last_param_type(sig: str) -> object:
    [macro] = parse_file(f"macro F({sig})\nx\nend\n", "t.uncursed").macros
    return macro.params[-1].type


# ── type grammar ─────────────────────────────────────────────────────


def test_bare_tuple_is_unbounded_of_tokens():
    assert _last_param_type("row: tuple") == VarTupleT(TokenT())


def test_explicit_token_ellipsis():
    assert _last_param_type("row: tuple<token...>") == VarTupleT(TokenT())


def test_named_tuple_element_type():
    assert _last_param_type("ps: tuple<tuple<k, v>...>") == VarTupleT(TupleT(("k", "v")))


def test_seq_element_type():
    assert _last_param_type("rows: tuple<seq<token>...>") == VarTupleT(SeqT(TokenT()))


def test_unbounded_tuple_inside_seq():
    assert _last_param_type("rows: seq<tuple<token...>>") == SeqT(VarTupleT(TokenT()))


def test_unbounded_tuple_inside_variadic():
    assert _last_param_type("rows: variadic<tuple>") == VariadicT(VarTupleT(TokenT()))


def test_tuple_of_single_name_is_still_a_named_tuple():
    # tuple<token> is a 1-tuple whose element is NAMED "token" — the
    # ellipsis, not the word, marks unboundedness
    assert _last_param_type("t: tuple<token>") == TupleT(("token",))


# ── is_empty() ───────────────────────────────────────────────────────


def test_is_empty_parses_as_condition():
    src = "macro F(row: tuple)\n@if is_empty(row)\nnil\n@end\nend\n"
    [macro] = parse_file(src, "t.uncursed").macros
    node = macro.body[0]
    assert isinstance(node, If)
    assert isinstance(node.cond, IsEmpty)


def test_is_empty_takes_exactly_one_argument():
    with pytest.raises(UncursedPpError):
        parse_file("macro F(a, b)\n@if is_empty(a, b)\nx\n@end\nend\n", "t.uncursed")


# ── error paths ──────────────────────────────────────────────────────


def test_named_access_on_unbounded_tuple_is_an_error():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source("macro F(row: tuple)\n{{row.first}}\nend\n", "t.uncursed")
    assert "no named elements" in str(excinfo.value)


def test_unpack_needs_a_named_tuple_element():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(
            "macro F(row: tuple<token...>)\n@for (a, b) in row\nx\n@end\nend\n",
            "t.uncursed",
        )
    assert "tuple unpacking" in str(excinfo.value)


def test_iterating_a_token_is_still_an_error():
    with pytest.raises(UncursedPpError):
        compile_source("macro F(x)\n@for a in x\n{{a}}\n@end\nend\n", "t.uncursed")


def test_len_on_plain_token_is_still_an_error():
    with pytest.raises(UncursedPpError):
        compile_source("macro F(x)\n{{len(x)}}\nend\n", "t.uncursed")


def test_len_accepts_unbounded_tuple():
    compile_source("macro F(row: tuple)\n{{len(row)}}\nend\n", "t.uncursed")


def test_unbounded_tuple_loops_compile():
    compile_source(
        "macro F(row: tuple)\n@for x in row\nf({{x}});\n@end\nend\n", "t.uncursed"
    )


def test_unpack_loop_over_pair_elements_compiles():
    compile_source(
        "macro F(ps: tuple<tuple<k, v>...>)\n@for (k, v) in ps\nset({{k}}, {{v}});\n@end\nend\n",
        "t.uncursed",
    )
