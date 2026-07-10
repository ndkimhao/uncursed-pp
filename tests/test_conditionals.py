"""@if/@else: parsing, condition forms, error paths.

Codegen shape and runtime behavior live in tests/golden/conditionals/.
"""

import pytest

from cursedpp.emitter import compile_source
from cursedpp.nodes import Cmp, If, Interp, IsParen, Join, Len, VarRef
from cursedpp.parser import CursedppError, parse_file


def test_parse_line_form_if_else():
    src = (
        "macro CTOR(name, args: seq<tuple<type, argname>>)\n"
        "@if len(args) == 1\n"
        "  one({{name}})\n"
        "@else\n"
        "  many({{name}})\n"
        "@end\n"
        "end\n"
    )
    [macro] = parse_file(src, "t.cursed").macros
    cond = next(n for n in macro.body if isinstance(n, If))
    assert cond.cond == Cmp(Len(VarRef("args")), "==", 1)
    assert any(isinstance(n, Interp) for n in cond.then)
    assert any(isinstance(n, Interp) for n in cond.else_)


def test_parse_inline_if_with_is_paren():
    src = "macro NORM(x)\n@if is_paren(x) {{remove_parens(x)}} @else {{x}} @end\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    cond = next(n for n in macro.body if isinstance(n, If))
    assert cond.cond == IsParen(VarRef("x"))
    assert cond.then and cond.else_


def test_parse_nested_inline_if_inside_inline_join():
    src = (
        "macro FOO(items: seq<token>)\n"
        'S{ @join items as it with ", ": @if is_paren(it) {{it}} @else ({{it}}, omit) @end@end }\n'
        "end\n"
    )
    [macro] = parse_file(src, "t.cursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    inner_if = next(n for n in join.body if isinstance(n, If))
    assert inner_if.cond == IsParen(VarRef("it"))


def test_parse_if_without_comparison_rejects_plain_expr():
    src = "macro F(x)\n@if x\nbody\n@end\nend\n"
    with pytest.raises(CursedppError):
        parse_file(src, "t.cursed")


def test_duplicate_else_is_error():
    src = "macro F(x)\n@if x == 1\na\n@else\nb\n@else\nc\n@end\nend\n"
    with pytest.raises(CursedppError) as excinfo:
        parse_file(src, "t.cursed")
    assert "duplicate @else" in str(excinfo.value)


def test_else_without_if_is_error():
    src = "macro F(x)\n@else\nend\n"
    with pytest.raises(CursedppError) as excinfo:
        parse_file(src, "t.cursed")
    assert "@else without" in str(excinfo.value)


def test_len_of_non_seq_is_error():
    with pytest.raises(CursedppError):
        compile_source("macro F(x)\n@if len(x) == 1\na\n@end\nend\n", "t.cursed")


def test_all_comparison_operators_map_to_boost_pp():
    for op, pp in [
        ("==", "EQUAL"),
        ("!=", "NOT_EQUAL"),
        ("<", "LESS"),
        (">", "GREATER"),
        ("<=", "LESS_EQUAL"),
        (">=", "GREATER_EQUAL"),
    ]:
        src = f"macro F(xs: seq<token>)\n@if len(xs) {op} 2\nbig\n@end\nend\n"
        out = compile_source(src, "f.cursed")
        assert f"BOOST_PP_{pp}(BOOST_PP_SEQ_SIZE(xs), 2)" in out


def test_empty_else_branch_emits_zero_param_helpers():
    src = "macro F(xs: seq<token>)\n@if len(xs) == 1\nonly\n@end\nend\n"
    out = compile_source(src, "f.cursed")
    # branches reference no variables, so the helpers take zero parameters
    assert "#define CURSEDPP_F_ELSE1()\n" in out
    assert "CURSEDPP_F_THEN1, CURSEDPP_F_ELSE1)()" in out
