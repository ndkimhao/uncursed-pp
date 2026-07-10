"""Named (keyword) parameters, including `named variadic` values.

Codegen shape and runtime behavior live in tests/golden/args/widget.*
and tests/golden/args/vec.* (named args composed with loops).
"""

import pytest

from cursedpp.emitter import compile_source
from cursedpp.parser import CursedppError, parse_file


def test_parse_named_params():
    src = (
        "macro MAKE_WIDGET(name, named WIDTH = 100, named HEIGHT = 50, named FLAGS = )\n"
        "struct widget {{name}} = { {{WIDTH}}, {{HEIGHT}}, {{FLAGS}} };\n"
        "end\n"
    )
    [macro] = parse_file(src, "t.cursed").macros
    assert [(p.name, p.default, p.named) for p in macro.params] == [
        ("name", None, False),
        ("WIDTH", "100", True),
        ("HEIGHT", "50", True),
        ("FLAGS", "", True),
    ]


def test_parse_named_variadic_param():
    src = "macro S(name, named variadic COLORS = none)\n{{COLORS}}\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    p = macro.params[1]
    assert (p.name, p.named, p.variadic_value, p.default) == ("COLORS", True, True, "none")


def test_setter_dispatch_one_define_per_keyword():
    src = (
        "macro W(name, named A = 1, named B = 2)\nf({{name}}, {{A}}, {{B}})\nend\n"
    )
    out = compile_source(src, "t.cursed")
    assert "#define CURSEDPP_W_SET_A(v) 0, v\n" in out
    assert "#define CURSEDPP_W_SET_B(v) 1, v\n" in out
    assert out.count("BOOST_PP_SEQ_FOLD_LEFT") == 1  # one fold, not one per keyword
    # slot updates are direct generated replacers, not TUPLE_REPLACE (which
    # hides a BOOST_PP_WHILE per keyword argument - measured 22x slower)
    assert "#define CURSEDPP_W_PUT_0(v, state) CURSEDPP_W_PUT_0_D(v, CURSEDPP_KW_SPREAD state)\n" in out
    assert "#define CURSEDPP_W_PUT_0_D(...) CURSEDPP_W_PUT_0_I(__VA_ARGS__)\n" in out
    assert "#define CURSEDPP_W_PUT_0_I(v, p0, p1) (v, p1)\n" in out
    assert "#define CURSEDPP_W_PUT_1_I(v, p0, p1) (p0, v)\n" in out
    assert "TUPLE_REPLACE" not in out


def test_named_variadic_setter_captures_commas():
    src = "macro S(name, named variadic COLORS = none)\n{{COLORS}}\nend\n"
    out = compile_source(src, "t.cursed")
    assert "#define CURSEDPP_S_SET_COLORS(...) 0, (__VA_ARGS__)\n" in out
    assert "BOOST_PP_REMOVE_PARENS(COLORS)" in out


def test_named_without_required_param_is_error():
    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(named A = 1)\n{{A}}\nend\n", "t.cursed")
    assert "required parameter" in str(excinfo.value)


def test_required_after_named_is_error():
    with pytest.raises(CursedppError):
        compile_source("macro F(a, named B = 1, c)\n{{a}}\nend\n", "t.cursed")


def test_single_keyword_put_arity():
    src = "macro S1(name, named ONLY = 7)\nf({{name}}, {{ONLY}})\nend\n"
    out = compile_source(src, "t.cursed")
    assert "#define CURSEDPP_S1_PUT_0_I(v, p0) (v)\n" in out
    assert "BOOST_PP_SEQ_FOLD_LEFT(CURSEDPP_S1_STEP, (7)," in out


def test_empty_default_in_fold_seed():
    src = "macro S2(name, named A = 1, named B = )\nf({{A}}, {{B}})\nend\n"
    out = compile_source(src, "t.cursed")
    assert "(1, )," in out  # empty default keeps its slot in the seed tuple
