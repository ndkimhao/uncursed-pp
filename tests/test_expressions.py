"""Expressions and @let: parsing and error paths.

Codegen shape and runtime behavior live in tests/golden/expressions/.
"""

import pytest

from conftest import canon, preprocess_src, requires_boost
from uncursed_pp.emitter import compile_source
from uncursed_pp.nodes import (
    Literal,
    Concat,
    ElemAccess,
    Interp,
    Let,
    RemoveParens,
    Stringize,
    VarRef,
)
from uncursed_pp.parser import UncursedPpError, parse_file


def test_parse_let_and_concat():
    src = '@macro G($f: tuple<$t, $n>)\n@let $g := concat(get_, $f.$n)\n{{$g}}\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    let = next(n for n in macro.body if isinstance(n, Let))
    assert let.name == "g"
    assert let.expr == Concat((Literal("get_"), ElemAccess(VarRef("f"), "n")))


def test_parse_index_access_and_remove_parens():
    src = '@macro F($xs: seq<token>)\n{{remove_parens($xs[0])}}\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    interp = next(n for n in macro.body if isinstance(n, Interp))
    assert interp.expr == RemoveParens(ElemAccess(VarRef("xs"), 0))


def test_parse_stringize():
    src = '@macro F($x)\n{{stringize($x)}}\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    interp = next(n for n in macro.body if isinstance(n, Interp))
    assert interp.expr == Stringize(VarRef("x"))


def test_unknown_function_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro F($x)\n{{mangle($x)}}\n@endmacro\n", "t.uncursed")
    assert "unknown function" in str(excinfo.value)


def test_function_arity_errors():
    for bad in ["concat(a)", "remove_parens(a, b)", "stringize()", "len(a, b)"]:
        with pytest.raises(UncursedPpError):
            parse_file(f"@macro F(a, b)\n{{{{{bad}}}}}\n@endmacro\n", "t.uncursed")


def test_named_access_on_non_tuple_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source('@macro F($x)\n{{$x.$field}}\n@endmacro\n', "t.uncursed")
    assert "tuple" in str(excinfo.value)


def test_unknown_tuple_field_is_error():
    src = '@macro F($p: tuple<$a, $b>)\n{{$p.$c}}\n@endmacro\n'
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "no element 'c'" in str(excinfo.value)


def test_indexing_non_seq_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source('@macro F($x)\n{{$x[0]}}\n@endmacro\n', "t.uncursed")
    assert "seq" in str(excinfo.value)


def test_undefined_variable_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source('@macro F($x)\n{{y}}\n@endmacro\n', "f.uncursed")
    assert "f.uncursed:2" in str(excinfo.value)
    assert "y" in str(excinfo.value)


def test_let_is_block_scoped():
    src = (
        '@macro F($xs: seq<token>)\n@for $x in $xs\n@let $y := concat($x, _sfx)\n{{$y}};\n@end\n{{$y}}\n@endmacro\n'
    )
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "undefined variable: $y" in str(excinfo.value)


def test_let_join_renders_one_helper_reused():
    src = (
        '@macro CALL2($fn, $args: seq<tuple<$type, $argname>>)\n@let $joined := @join $args with ", ": {{$argname}}@end\n{{$fn}}({{$joined}}, {{$joined}})\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")
    assert out.count("#define UNCURSED_PP_CALL2_EACH1") == 1  # one helper family
    # the @let renders the join once; both use sites reuse the same call
    assert out.count("UNCURSED_PP_CALL2_PICK1(BOOST_PP_SEQ_SIZE(args))(args)") == 2


def test_let_rejects_mixed_inline_and_text():
    src = '@macro F(xs: seq<token>)\n@let j := prefix @join xs with ",": {{xs}}@end\n{{j}}\n@endmacro\n'
    with pytest.raises(UncursedPpError):
        parse_file(src, "t.uncursed")


# ── AP unpacking for tuple-typed macro params ───────────────────────


def test_tuple_param_spreads_into_body_define():
    src = '@macro PAIR2($p: tuple<$a, $b>)\nS{ {{$p.$a}} | {{$p.$b}} }\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_PAIR2_BODY1(a, b) S{ a | b }\n" in out
    assert "#define UNCURSED_PP_PAIR2_BODY1_D(...) UNCURSED_PP_PAIR2_BODY1(__VA_ARGS__)\n" in out
    assert "#define PAIR2(p) UNCURSED_PP_PAIR2_BODY1_D(UNCURSED_PP_KW_SPREAD p)\n" in out
    assert "TUPLE_ELEM" not in out


def test_tuple_param_mixed_with_plain_params():
    src = '@macro G($pre, $f: tuple<$t, $n>)\n{{$pre}} {{$f.$t}} {{$f.$n}};\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_G_BODY1(pre, t, n) pre t n;\n" in out
    assert "#define G(pre, f) UNCURSED_PP_G_BODY1_D(pre, UNCURSED_PP_KW_SPREAD f)\n" in out


def test_whole_tuple_use_falls_back_to_tuple_elem():
    src = '@macro H($p: tuple<$a, $b>)\nfirst {{$p.$a}} whole {{$p}}\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "BOOST_PP_TUPLE_ELEM(0, p)" in out
    assert "BODY1" not in out


def test_two_tuple_params_both_spread():
    src = '@macro Z($p: tuple<$a, $b>, $q: tuple<$c, $d2>)\n{{$p.$a}}{{$q.$c}} {{$p.$b}}{{$q.$d2}};\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_Z_BODY1(a, b, c, d2)" in out
    assert (
        "#define Z(p, q) UNCURSED_PP_Z_BODY1_D(UNCURSED_PP_KW_SPREAD p, UNCURSED_PP_KW_SPREAD q)\n"
        in out
    )


def test_field_collision_with_param_falls_back():
    src = '@macro Y($a, $p: tuple<$a, $b>)\n{{$a}} {{$p.$a}} {{$p.$b}};\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "BODY1" not in out
    assert "BOOST_PP_TUPLE_ELEM(0, p)" in out


def test_whole_use_in_condition_falls_back():
    src = '@macro W2($p: tuple<$a, $b>)\n@if is_paren($p) yes @else no @end\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "BODY1" not in out
    assert "BOOST_PP_IS_BEGIN_PARENS(p)" in out


def test_tuple_param_with_tail_defaults_keeps_tuple_elem():
    src = '@macro D2($p: tuple<$a, $b>, $lvl = 0)\n{{$p.$a}} {{$lvl}};\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    # spread applies only to the simple dispatch path
    assert "BODY1" not in out
    assert "BOOST_PP_TUPLE_ELEM(0, p)" in out


def test_concat_four_plus_args_uses_kary_paste():
    src = '@macro F($a, $b)\n{{concat(pre_, $a, _mid_, $b, _end)}}\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_F_CAT5(p0, p1, p2, p3, p4) UNCURSED_PP_F_CAT5_I(p0, p1, p2, p3, p4)\n" in out
    assert "#define UNCURSED_PP_F_CAT5_I(p0, p1, p2, p3, p4) p0 ## p1 ## p2 ## p3 ## p4\n" in out
    assert "UNCURSED_PP_F_CAT5(pre_, a, _mid_, b, _end)" in out
    assert "BOOST_PP_CAT" not in out


def test_concat_three_args_keeps_nested_cat():
    src = '@macro F($a)\n{{concat(pre_, $a, _end)}}\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    # 3-arg nesting measured 1.06x - below the bar, keep BOOST_PP_CAT
    assert "BOOST_PP_CAT(pre_, BOOST_PP_CAT(a, _end))" in out
    assert "CAT3" not in out


# ── spread safety: fall back to TUPLE_ELEM when spreading can't work ──


SPREAD_IF_SRC = (
    '@macro T($xs: seq<token>, $p: tuple<$a, $b>)\n@if len($xs) == 1\n{{$p.$a}}\n@else\n{{$p.$b}}\n@end\n@endmacro\n'
)

SPREAD_VARIADIC_SRC = (
    '@macro V($p: tuple<$a, $b>, $items: variadic)\n{{$p.$a}}: @join $items as $it with ", ": {{$it}}@end\n@endmacro\n'
)


def test_spread_skipped_when_body_has_blocks():
    out = compile_source(SPREAD_IF_SRC, "t.uncursed")
    # branch helpers can only transport the whole tuple, so no spreading
    assert "BODY1" not in out
    assert "BOOST_PP_TUPLE_ELEM(0, p)" in out


def test_spread_skipped_with_variadic_param():
    out = compile_source(SPREAD_VARIADIC_SRC, "t.uncursed")
    assert "BODY1" not in out
    assert "#define V(p, ...)" in out


@requires_boost
def test_spread_fallback_expands_correctly(tmp_path):
    out = preprocess_src(tmp_path, SPREAD_IF_SRC, "sif", "T((q), (foo, bar))")
    assert canon("foo") == out
    out = preprocess_src(
        tmp_path, SPREAD_VARIADIC_SRC, "svar", "V((foo, bar), a, b, c)"
    )
    assert canon("foo: a, b, c") == out


def test_let_join_used_inside_loop_is_rejected():
    src = (
        '@macro F($xs: seq<token>, $ys: seq<token>)\n@let $j := @join $ys as $y with ", ": {{$y}}@end\n@for $x in $xs\ng({{$x}}, {{$j}});\n@end\n@endmacro\n'
    )
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "inline" in str(excinfo.value)


def test_let_join_used_inside_if_branch_is_rejected():
    src = (
        '@macro F($xs: seq<token>)\n@let $j := @join $xs as $x with ", ": {{$x}}@end\n@if len($xs) == 1\ng({{$j}});\n@end\n@endmacro\n'
    )
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "inline" in str(excinfo.value)
