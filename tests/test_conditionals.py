"""@if/@else: parsing, condition forms, error paths.

Codegen shape and runtime behavior live in tests/golden/conditionals/.
"""

import pytest

from conftest import canon, preprocess_src, requires_boost
from uncursed_pp.emitter import compile_source
from uncursed_pp.nodes import Cmp, If, Interp, IsParen, Join, Len, VarRef
from uncursed_pp.parser import UncursedPpError, parse_file


def test_parse_line_form_if_else():
    src = (
        "@macro CTOR(name, args: seq<tuple<type, argname>>)\n"
        "@if len(args) == 1\n"
        "  one({{name}})\n"
        "@else\n"
        "  many({{name}})\n"
        "@end\n"
        "@endmacro\n"
    )
    [macro] = parse_file(src, "t.uncursed").macros
    cond = next(n for n in macro.body if isinstance(n, If))
    assert cond.cond == Cmp(Len(VarRef("args")), "==", 1)
    assert any(isinstance(n, Interp) for n in cond.then)
    assert any(isinstance(n, Interp) for n in cond.else_)


def test_parse_inline_if_with_is_paren():
    src = "@macro NORM(x)\n@if is_paren(x) {{remove_parens(x)}} @else {{x}} @end\n@endmacro\n"
    [macro] = parse_file(src, "t.uncursed").macros
    cond = next(n for n in macro.body if isinstance(n, If))
    assert cond.cond == IsParen(VarRef("x"))
    assert cond.then and cond.else_


def test_parse_nested_inline_if_inside_inline_join():
    src = (
        "@macro FOO(items: seq<token>)\n"
        'S{ @join items as it with ", ": @if is_paren(it) {{it}} @else ({{it}}, omit) @end@end }\n'
        "@endmacro\n"
    )
    [macro] = parse_file(src, "t.uncursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    inner_if = next(n for n in join.body if isinstance(n, If))
    assert inner_if.cond == IsParen(VarRef("it"))


def test_parse_if_without_comparison_rejects_plain_expr():
    src = "@macro F(x)\n@if x\nbody\n@end\n@endmacro\n"
    with pytest.raises(UncursedPpError):
        parse_file(src, "t.uncursed")


def test_duplicate_else_is_error():
    src = "@macro F(x)\n@if x == 1\na\n@else\nb\n@else\nc\n@end\n@endmacro\n"
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file(src, "t.uncursed")
    assert "duplicate @else" in str(excinfo.value)


def test_else_without_if_is_error():
    src = "@macro F(x)\n@else\n@endmacro\n"
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file(src, "t.uncursed")
    assert "@else without" in str(excinfo.value)


def test_len_of_non_seq_is_error():
    with pytest.raises(UncursedPpError):
        compile_source("@macro F(x)\n@if len(x) == 1\na\n@end\n@endmacro\n", "t.uncursed")


def test_equality_operators_map_to_boost_pp():
    for op, pp in [("==", "EQUAL"), ("!=", "NOT_EQUAL")]:
        src = f"@macro F(xs: seq<token>)\n@if len(xs) {op} 2\nbig\n@end\n@endmacro\n"
        out = compile_source(src, "f.uncursed")
        assert f"BOOST_PP_{pp}(BOOST_PP_SEQ_SIZE(xs), 2)" in out


def test_relational_operators_compile_to_dec_chains():
    # DEC^k + BOOL replaces the WHILE-based LESS/GREATER family; < and <=
    # swap branch order (BOOL(DEC^k(x)) is 1 iff x > k)
    for op, k, swapped in [("<", 1, True), ("<=", 2, True), (">", 2, False), (">=", 1, False)]:
        src = f"@macro F(xs: seq<token>)\n@if len(xs) {op} 2\nbig\n@else\nsmall\n@end\n@endmacro\n"
        out = compile_source(src, "f.uncursed")
        expr = "BOOST_PP_SEQ_SIZE(xs)"
        for _ in range(k):
            expr = f"BOOST_PP_DEC({expr})"
        order = "UNCURSED_PP_F_ELSE1, UNCURSED_PP_F_THEN1" if swapped else "UNCURSED_PP_F_THEN1, UNCURSED_PP_F_ELSE1"
        assert f"BOOST_PP_IIF(BOOST_PP_BOOL({expr}), {order})()" in out, (op, out)
        assert "BOOST_PP_LESS" not in out and "BOOST_PP_GREATER" not in out


def test_empty_else_branch_emits_zero_param_helpers():
    src = "@macro F(xs: seq<token>)\n@if len(xs) == 1\nonly\n@end\n@endmacro\n"
    out = compile_source(src, "f.uncursed")
    # branches reference no variables, so the helpers take zero parameters
    assert "#define UNCURSED_PP_F_ELSE1()\n" in out
    assert "UNCURSED_PP_F_THEN1, UNCURSED_PP_F_ELSE1)()" in out


# ── relational ops compile to saturating DEC chains, not WHILE-based SUB ──


def test_less_than_compiles_to_dec_chain_with_swapped_branches():
    src = "@macro F(x)\n@if x < 3 small @else big @end\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    # x < 3  <=>  DEC^2(x) saturates to 0; branch order swaps so BOOL=0 -> THEN
    assert (
        "BOOST_PP_IIF(BOOST_PP_BOOL(BOOST_PP_DEC(BOOST_PP_DEC(x))), "
        "UNCURSED_PP_F_ELSE1, UNCURSED_PP_F_THEN1)()" in out
    )
    assert "BOOST_PP_LESS" not in out
    assert "#include <boost/preprocessor/arithmetic/dec.hpp>" in out
    assert "#include <boost/preprocessor/logical/bool.hpp>" in out


def test_greater_equal_one_is_plain_bool():
    src = "@macro F(x)\n@if x >= 1 some @else none @end\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "BOOST_PP_IIF(BOOST_PP_BOOL(x), UNCURSED_PP_F_THEN1, UNCURSED_PP_F_ELSE1)()" in out


def test_len_greater_zero_is_bool_of_seq_size():
    src = "@macro F(xs: seq<token>)\n@if len(xs) > 0 has @end\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "BOOST_PP_IIF(BOOST_PP_BOOL(BOOST_PP_SEQ_SIZE(xs)), UNCURSED_PP_F_THEN1, UNCURSED_PP_F_ELSE1)()" in out
    assert "BOOST_PP_GREATER" not in out


def test_less_than_zero_constant_folds():
    src = "@macro F(x)\n@if x < 0 never @else always @end\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "UNCURSED_PP_F_ELSE1()" in out
    assert "IIF" not in out


def test_equality_keeps_boost_pp_equal():
    src = "@macro F(x)\n@if x == 3 eq @end\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "BOOST_PP_EQUAL(x, 3)" in out


@requires_boost
def test_dec_chain_relationals_expand(tmp_path):
    src = (
        "@macro CMP(x)\n"
        "@if x < 3 lt3 @else ge3 @end / @if x >= 2 ge2 @else lt2 @end / "
        "@if x <= 1 le1 @else gt1 @end / @if x > 4 gt4 @else le4 @end\n"
        "@endmacro\n"
    )
    out = preprocess_src(tmp_path, src, "cmp", "s(CMP(0))\ns(CMP(2))\ns(CMP(5))")
    assert canon("s(lt3 / lt2 / le1 / le4)") in out
    assert canon("s(lt3 / ge2 / gt1 / le4)") in out
    assert canon("s(ge3 / ge2 / gt1 / gt4)") in out


def test_else_with_trailing_text_is_error():
    src = "@macro M(xs: seq<token>)\n@if len(xs) == 1\none\n@else if len(xs) == 2\ntwo\n@end\n@endmacro\n"
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file(src, "t.uncursed")
    assert "@else" in str(excinfo.value) and "t.uncursed:4" in str(excinfo.value)


def test_comparison_literal_above_256_is_error():
    for cond in ["len(xs) == 300", "len(xs) != 257", "x == 999"]:
        src = f"@macro F(x, xs: seq<token>)\n@if {cond}\nbig\n@end\n@endmacro\n"
        with pytest.raises(UncursedPpError) as excinfo:
            compile_source(src, "t.uncursed")
        assert "256" in str(excinfo.value)


def test_comparison_literal_at_256_is_ok():
    src = "@macro F(xs: seq<token>)\n@if len(xs) == 256\nmax\n@end\n@endmacro\n"
    compile_source(src, "t.uncursed")  # boundary value is legal
