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
    # no fold at all: OVERLOAD knows the exact kwarg count, so each arity
    # nests a one-step setter over BARE comma-separated state (audit: 8.5x)
    assert "SEQ_FOLD_LEFT" not in out
    assert "VARIADIC_TO_SEQ" not in out
    assert "#define CURSEDPP_W_STEP1(e, ...) CURSEDPP_W_STEP_D(BOOST_PP_CAT(CURSEDPP_W_SET_, e), __VA_ARGS__)\n" in out
    assert "#define CURSEDPP_W_STEP_D(...) CURSEDPP_W_STEP_I(__VA_ARGS__)\n" in out
    assert "#define CURSEDPP_W_STEP_I(i, v, ...) CURSEDPP_W_PUT_ ## i(v, __VA_ARGS__)\n" in out
    assert "#define CURSEDPP_W_PUT_0(v, p0, p1) v, p1\n" in out
    assert "#define CURSEDPP_W_PUT_1(v, p0, p1) p0, v\n" in out
    assert "#define CURSEDPP_W_BODY_D(...) CURSEDPP_W_BODY(__VA_ARGS__)\n" in out
    assert "#define CURSEDPP_W_2(name, e1) CURSEDPP_W_BODY_D(name, CURSEDPP_W_STEP1(e1, 1, 2))\n" in out
    assert "#define CURSEDPP_W_3(name, e1, e2) CURSEDPP_W_BODY_D(name, CURSEDPP_W_STEP1(e2, CURSEDPP_W_STEP1(e1, 1, 2)))\n" in out
    assert "UNPACK" not in out
    assert "KW_SPREAD" not in out


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
    assert "#define CURSEDPP_S1_PUT_0(v, p0) v\n" in out
    assert "#define CURSEDPP_S1_2(name, e1) CURSEDPP_S1_BODY_D(name, CURSEDPP_S1_STEP1(e1, 7))\n" in out


def test_empty_default_keeps_its_slot_in_the_state():
    src = "macro S2(name, named A = 1, named B = )\nf({{A}}, {{B}})\nend\n"
    out = compile_source(src, "t.cursed")
    # empty default keeps its slot in the bare state list
    assert "CURSEDPP_S2_STEP1(e1, 1, )" in out
    assert "#define CURSEDPP_S2_1(name) CURSEDPP_S2_BODY(name, 1, )\n" in out
