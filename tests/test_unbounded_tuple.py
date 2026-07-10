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
    [macro] = parse_file(f"@macro F({sig})\nx\n@endmacro\n", "t.uncursed").macros
    return macro.params[-1].type


# ── type grammar ─────────────────────────────────────────────────────


def test_bare_tuple_is_unbounded_of_tokens():
    assert _last_param_type("$row: tuple") == VarTupleT(TokenT())


def test_explicit_token_ellipsis():
    assert _last_param_type("$row: tuple<token...>") == VarTupleT(TokenT())


def test_named_tuple_element_type():
    assert _last_param_type("$ps: tuple<tuple<$k, $v>...>") == VarTupleT(TupleT(("k", "v")))


def test_seq_element_type():
    assert _last_param_type("$rows: tuple<seq<token>...>") == VarTupleT(SeqT(TokenT()))


def test_unbounded_tuple_inside_seq():
    assert _last_param_type("$rows: seq<tuple<token...>>") == SeqT(VarTupleT(TokenT()))


def test_unbounded_tuple_inside_variadic():
    assert _last_param_type("$rows: variadic<tuple>") == VariadicT(VarTupleT(TokenT()))


def test_tuple_of_single_name_is_still_a_named_tuple():
    # tuple<token> is a 1-tuple whose element is NAMED "token" — the
    # ellipsis, not the word, marks unboundedness
    assert _last_param_type("$t: tuple<$token>") == TupleT(("token",))


# ── is_empty() ───────────────────────────────────────────────────────


def test_is_empty_parses_as_condition():
    src = '@macro F($row: tuple)\n@if is_empty($row)\nnil\n@end\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    node = macro.body[0]
    assert isinstance(node, If)
    assert isinstance(node.cond, IsEmpty)


def test_is_empty_takes_exactly_one_argument():
    with pytest.raises(UncursedPpError):
        parse_file("@macro F(a, b)\n@if is_empty(a, b)\nx\n@end\n@endmacro\n", "t.uncursed")


def test_is_empty_is_a_whole_value_use(tmp_path):
    # is_empty($a) must consult the tuple itself: the spread pass used to
    # erase $a (IsEmpty was missing from the whole-use walk), leaving the
    # probe reading a stray identifier a call-site '#define a' could flip
    from conftest import CC, canon, preprocess_src

    if CC is None:
        pytest.skip("no C compiler available")
    src = '@macro S2($a: tuple<$x, $y>)\n{{$a.$x}}/{{$a.$y}} empty={{is_empty($a)}}\n@endmacro\n'
    out = preprocess_src(tmp_path, src, "ie", "#define a\nS2((3, 4))")
    assert canon("3/4 empty=0") in out


# ── error paths ──────────────────────────────────────────────────────


def test_named_access_on_unbounded_tuple_is_an_error():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source('@macro F($row: tuple)\n{{$row.$first}}\n@endmacro\n', "t.uncursed")
    assert "no named elements" in str(excinfo.value)


def test_unpack_needs_a_named_tuple_element():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(
            '@macro F($row: tuple)\n@for ($a, $b) in $row\nx\n@end\n@endmacro\n',
            "t.uncursed",
        )
    assert "tuple unpacking" in str(excinfo.value)


def test_iterating_a_token_is_still_an_error():
    with pytest.raises(UncursedPpError):
        compile_source('@macro F($x)\n@for $a in $x\n{{$a}}\n@end\n@endmacro\n', "t.uncursed")


def test_len_on_plain_token_is_still_an_error():
    with pytest.raises(UncursedPpError):
        compile_source('@macro F($x)\n{{len($x)}}\n@endmacro\n', "t.uncursed")


def test_len_accepts_unbounded_tuple():
    compile_source('@macro F($row: tuple)\n{{len($row)}}\n@endmacro\n', "t.uncursed")


def test_unbounded_tuple_loops_compile():
    compile_source(
        '@macro F($row: tuple)\n@for $x in $row\nf({{$x}});\n@end\n@endmacro\n', "t.uncursed"
    )


def test_unpack_loop_over_pair_elements_compiles():
    compile_source(
        '@macro F($ps: tuple<tuple<$k, $v>...>)\n@for ($k, $v) in $ps\nset({{$k}}, {{$v}});\n@end\n@endmacro\n',
        "t.uncursed",
    )


# ── hybrid: fixed named head + unbounded tail ────────────────────────


def test_hybrid_type_parses():
    assert _last_param_type("$f: tuple<$fname, $ftype, token...>") == VarTupleT(
        TokenT(), ("fname", "ftype")
    )


def test_hybrid_with_typed_tail():
    assert _last_param_type("$f: tuple<$key, tuple<$a, $b>...>") == VarTupleT(
        TupleT(("a", "b")), ("key",)
    )


def test_hybrid_inside_seq():
    assert _last_param_type("$fs: seq<tuple<$n, token...>>") == SeqT(
        VarTupleT(TokenT(), ("n",))
    )


def test_hybrid_named_head_access_compiles():
    compile_source(
        '@macro F($f: tuple<$fname, $ftype, token...>)\n{{$f.$fname}} {{$f.$ftype}}\n@endmacro\n',
        "t.uncursed",
    )


def test_hybrid_unknown_field_lists_names():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(
            '@macro F($f: tuple<$fname, $ftype, token...>)\n{{$f.$nope}}\n@endmacro\n',
            "t.uncursed",
        )
    assert "fname" in str(excinfo.value) and "ftype" in str(excinfo.value)


def test_hybrid_tail_ops_compile():
    compile_source(
        '@macro F($f: tuple<$n, token...>)\n{{$f.$n}}: {{len($f)}} {{$f[0]}}\n@for $a in $f\n[{{$a}}]\n@end\n@endmacro\n',
        "t.uncursed",
    )


def test_hybrid_unpack_needs_tuple_tail():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(
            '@macro F($f: tuple<$n, token...>)\n@for ($a, $b) in $f\nx\n@end\n@endmacro\n',
            "t.uncursed",
        )
    assert "tuple unpacking" in str(excinfo.value)
