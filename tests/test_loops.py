"""@for and @join: parsing and error paths.

Codegen shape and runtime behavior live in tests/golden/loops/ (exact
header comparison + #? invocation specs) - not as string assertions here.
"""

import pytest

from cursedpp.emitter import compile_source
from cursedpp.nodes import ForEach, Interp, Join, VarRef
from cursedpp.parser import CursedppError, parse_file


def test_parse_for_loop_body():
    src = (
        "macro DECLARE_FIELDS(fields: seq<tuple<type, name>>)\n"
        "@for (type, name) in fields\n"
        "  {{type}} {{name}};\n"
        "@end\n"
        "end\n"
    )
    [macro] = parse_file(src, "test.cursed").macros
    [loop] = [n for n in macro.body if isinstance(n, ForEach)]
    assert loop.unpack == ("type", "name")
    assert loop.var is None
    assert loop.iterable == "fields"
    interps = [n for n in loop.body if isinstance(n, Interp)]
    assert [i.expr for i in interps] == [VarRef("type"), VarRef("name")]


def test_parse_inline_join():
    src = (
        "macro PROTO(name, args: seq<tuple<type, argname>>)\n"
        'void {{name}}(@join args with ", ": {{type}} {{argname}}@end);\n'
        "end\n"
    )
    [macro] = parse_file(src, "t.cursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.iterable == "args"
    assert join.sep == ", "
    assert join.var is None
    interps = [n for n in join.body if isinstance(n, Interp)]
    assert [i.expr for i in interps] == [VarRef("type"), VarRef("argname")]


def test_parse_line_form_join_with_as_binding():
    src = 'macro ORS(xs: seq<token>)\n@join xs as x with " || "\n({{x}})\n@end\nend\n'
    [macro] = parse_file(src, "t.cursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.var == "x"
    assert join.sep == " || "
    assert join.iterable == "xs"


def test_inline_join_missing_end_is_error():
    src = 'macro P(xs: seq<token>)\nf(@join xs with ", ": {{xs}});\nend\n'
    with pytest.raises(CursedppError) as excinfo:
        parse_file(src, "t.cursed")
    assert "t.cursed:2" in str(excinfo.value)


def test_unclosed_line_form_loop_is_error():
    src = "macro F(xs: seq<token>)\n@for x in xs\n{{x}}\nend\n"
    with pytest.raises(CursedppError):
        parse_file(src, "t.cursed")


def test_iterating_non_seq_is_error():
    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(x)\n@for a in x\n{{a}}\n@end\nend\n", "t.cursed")
    assert "non-seq" in str(excinfo.value)


def test_iterating_undefined_name_is_error():
    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(x)\n@for a in nope\n{{a}}\n@end\nend\n", "t.cursed")
    assert "undefined" in str(excinfo.value)


def test_unpack_arity_mismatch_is_error():
    src = "macro F(xs: seq<tuple<a, b>>)\n@for (a, b, c) in xs\n{{a}}\n@end\nend\n"
    with pytest.raises(CursedppError) as excinfo:
        compile_source(src, "t.cursed")
    assert "arity" in str(excinfo.value)


def test_unpacking_non_tuple_seq_is_error():
    src = "macro F(xs: seq<token>)\n@for (a, b) in xs\n{{a}}\n@end\nend\n"
    with pytest.raises(CursedppError) as excinfo:
        compile_source(src, "t.cursed")
    assert "tuple" in str(excinfo.value)


NESTED_FOR = (
    "macro CROSS(xs: seq<token>, ys: seq<token>)\n"
    "@for x in xs\n@for y in ys\npair({{x}}, {{y}});\n@end\n@end\nend\n"
)

LOOP_IF_LOOP = (
    "macro DEEP(xs: seq<token>, ys: seq<token>)\n"
    "@for x in xs\n"
    "@if len(ys) == 1\n"
    "@for y in ys\np({{y}});\n@end\n"
    "@end\n"
    "@end\nend\n"
)

JOIN_IN_FOR = (
    "macro JF(xs: seq<token>, ys: seq<token>)\n"
    "@for x in xs\n"
    'f(@join ys as y with ", ": {{y}}@end);\n'
    "@end\nend\n"
)


def test_nested_for_is_rejected():
    with pytest.raises(CursedppError) as excinfo:
        compile_source(NESTED_FOR, "t.cursed")
    assert "nested loops" in str(excinfo.value)


def test_loop_if_loop_is_rejected():
    with pytest.raises(CursedppError) as excinfo:
        compile_source(LOOP_IF_LOOP, "t.cursed")
    assert "nested loops" in str(excinfo.value)


def test_inline_join_inside_for_is_rejected():
    with pytest.raises(CursedppError) as excinfo:
        compile_source(JOIN_IN_FOR, "t.cursed")
    assert "nested loops" in str(excinfo.value)


def test_let_join_inside_loop_is_rejected():
    src = (
        "macro F(xs: seq<token>, ys: seq<token>)\n"
        "@for x in xs\n"
        '@let j := @join ys as y with ", ": {{y}}@end\n'
        "g({{j}});\n"
        "@end\nend\n"
    )
    with pytest.raises(CursedppError) as excinfo:
        compile_source(src, "t.cursed")
    assert "nested loops" in str(excinfo.value)


def test_sibling_loops_are_fine():
    src = (
        "macro TWO(xs: seq<token>)\n"
        "@for x in xs\na({{x}});\n@end\n"
        "@for x in xs\nb({{x}});\n@end\n"
        "end\n"
    )
    out = compile_source(src, "t.cursed")
    # (the near-identical bodies collapse into one shared d-parameterized
    # helper - the point is both call sites exist and nothing was rejected)
    assert out.count("BOOST_PP_SEQ_FOR_EACH(") == 2


def test_loop_inside_if_branch_is_fine():
    src = (
        "macro OPT(xs: seq<token>)\n"
        "@if len(xs) == 1\nsolo({{xs[0]}})\n@else\n"
        "@for x in xs\nmany({{x}});\n@end\n"
        "@end\nend\n"
    )
    out = compile_source(src, "t.cursed")  # no loop encloses the @if
    assert "CURSEDPP_OPT_EACH1" in out

# ── AP unpacking: tuple elements become direct macro params ─────────


def test_tuple_loop_unpacks_via_ap_juxtaposition():
    src = "macro DECL(fields: seq<tuple<type, name>>)\n@for (type, name) in fields\n  {{type}} {{name}};\n@end\nend\n"
    out = compile_source(src, "t.cursed")
    assert "#define CURSEDPP_DECL_AP1(type, name) type name;\n" in out
    assert "#define CURSEDPP_DECL_EACH1(r, d, e) CURSEDPP_DECL_AP1 e\n" in out
    assert "TUPLE_ELEM" not in out


def test_tuple_loop_with_free_var_spreads_through_d():
    src = (
        "macro TBL(sname, fields: seq<tuple<type, name>>)\n"
        "@for (type, name) in fields\n"
        "  { {{stringize(name)}}, offsetof({{sname}}, {{name}}) },\n"
        "@end\n"
        "end\n"
    )
    out = compile_source(src, "t.cursed")
    assert (
        "#define CURSEDPP_TBL_AP1(sname, type, name) "
        "{ BOOST_PP_STRINGIZE(name), offsetof(sname, name) },\n" in out
    )
    assert "#define CURSEDPP_TBL_AP1_D(...) CURSEDPP_TBL_AP1(__VA_ARGS__)\n" in out
    assert (
        "#define CURSEDPP_TBL_EACH1(r, d, e) CURSEDPP_TBL_AP1_D(d, CURSEDPP_KW_SPREAD e)\n"
        in out
    )
    assert "BOOST_PP_SEQ_FOR_EACH(CURSEDPP_TBL_EACH1, sname, fields)" in out
    assert '#include "cursedpp_runtime.h"' in out
    assert "TUPLE_ELEM" not in out


def test_as_binding_keeps_element_form():
    src = "macro F(xs: seq<tuple<a, b>>)\n@for x in xs\ng({{x}});\n@end\nend\n"
    out = compile_source(src, "t.cursed")
    assert "#define CURSEDPP_F_EACH1(r, d, e) g(e);\n" in out
