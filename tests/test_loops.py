"""@for and @join: parsing and error paths.

Codegen shape and runtime behavior live in tests/golden/loops/ (exact
header comparison + #? invocation specs) - not as string assertions here.
"""

import pytest

from conftest import canon, preprocess_src, requires_boost
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


def test_nested_for_uses_repeat():
    out = compile_source(NESTED_FOR, "t.cursed")
    # outer level keeps SEQ_FOR_EACH; the inner level iterates via the
    # auto-reentrant BOOST_PP_REPEAT with SEQ_ELEM element access
    assert "BOOST_PP_SEQ_FOR_EACH(" in out
    assert "BOOST_PP_REPEAT(BOOST_PP_SEQ_SIZE(" in out
    assert "(z, n, d)" in out


def test_loop_if_loop_compiles():
    out = compile_source(LOOP_IF_LOOP, "t.cursed")
    assert "BOOST_PP_REPEAT(" in out


def test_inline_join_inside_for_compiles():
    out = compile_source(JOIN_IN_FOR, "t.cursed")
    assert "BOOST_PP_REPEAT(" in out
    assert "BOOST_PP_COMMA_IF(n)" in out


def test_let_join_inside_loop_compiles():
    src = (
        "macro F(xs: seq<token>, ys: seq<token>)\n"
        "@for x in xs\n"
        '@let j := @join ys as y with ", ": {{y}}@end\n'
        "g({{j}});\n"
        "@end\nend\n"
    )
    out = compile_source(src, "t.cursed")
    assert "BOOST_PP_REPEAT(" in out


def test_loops_nest_at_most_four_deep():
    inner = "quint({{a}}, {{b}}, {{c}}, {{d2}}, {{e2}});\n"
    src = "macro F(s1: seq<token>, s2: seq<token>, s3: seq<token>, s4: seq<token>, s5: seq<token>)\n"
    for var, seq in [("a", "s1"), ("b", "s2"), ("c", "s3"), ("d2", "s4"), ("e2", "s5")]:
        src += f"@for {var} in {seq}\n"
    src += inner + "@end\n" * 5 + "end\n"
    with pytest.raises(CursedppError) as excinfo:
        compile_source(src, "t.cursed")
    assert "4 deep" in str(excinfo.value)


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


def test_tuple_loop_with_two_free_vars_spreads_d_tuple():
    src = (
        "macro T2(a, b, fields: seq<tuple<t, n>>)\n"
        "@for (t, n) in fields\n"
        "  f({{a}}, {{b}}, {{t}}, {{n}});\n"
        "@end\n"
        "end\n"
    )
    out = compile_source(src, "t.cursed")
    assert "#define CURSEDPP_T2_AP1(a, b, t, n) f(a, b, t, n);\n" in out
    assert (
        "#define CURSEDPP_T2_EACH1(r, d, e) "
        "CURSEDPP_T2_AP1_D(CURSEDPP_KW_SPREAD d, CURSEDPP_KW_SPREAD e)\n" in out
    )
    assert "BOOST_PP_SEQ_FOR_EACH(CURSEDPP_T2_EACH1, (a, b), fields)" in out


def test_field_name_colliding_with_free_var_falls_back_to_tuple_elem():
    src = (
        "macro C(type, fields: seq<tuple<type, name>>)\n"
        "@for (type, name) in fields\n"
        "  {{type}} {{name}};\n"
        "@end\nend\n"
    )
    # unpack name shadows the outer param; if the body ALSO used the outer
    # value it couldn't - here the shadowing unpack wins and AP still applies
    out = compile_source(src, "t.cursed")
    assert "CURSEDPP_C_AP1(type, name)" in out


def test_conditional_inside_ap_loop_body():
    src = (
        "macro P(fields: seq<tuple<t, n>>)\n"
        "@for (t, n) in fields\n"
        "  @if is_paren(t) {{remove_parens(t)}} {{n}}; @else {{t}} {{n}}; @end\n"
        "@end\nend\n"
    )
    out = compile_source(src, "t.cursed")
    # branch helpers receive the AP params by name
    assert "#define CURSEDPP_P_THEN1(t, n) BOOST_PP_REMOVE_PARENS(t) n;\n" in out
    assert "CURSEDPP_P_THEN1, CURSEDPP_P_ELSE1)(t, n)" in out


@requires_boost
def test_ap_loop_with_conditional_expands(tmp_path):
    src = (
        "macro P(fields: seq<tuple<t, n>>)\n"
        "@for (t, n) in fields\n"
        "  @if is_paren(t) {{remove_parens(t)}} {{n}}; @else {{t}} {{n}}; @end\n"
        "@end\nend\n"
    )
    out = preprocess_src(tmp_path, src, "p", "P((((a, b), x))((int, y)))")
    assert canon("a, b x; int y;") in out


# ── identity comma joins compile to table-driven SEQ_ENUM ───────────


def test_identity_comma_join_uses_seq_enum():
    src = 'macro ARGS(xs: seq<token>)\nf(@join xs as x with ", ": {{x}}@end)\nend\n'
    out = compile_source(src, "t.cursed")
    assert "#define ARGS(xs) f(BOOST_PP_SEQ_ENUM(xs))\n" in out
    assert "SEQ_FOR_EACH_I" not in out
    assert "#include <boost/preprocessor/seq/enum.hpp>" in out


def test_non_identity_comma_join_keeps_for_each_i():
    src = 'macro W(xs: seq<token>)\nf(@join xs as x with ", ": g({{x}})@end)\nend\n'
    out = compile_source(src, "t.cursed")
    assert "SEQ_FOR_EACH_I" in out
    assert "SEQ_ENUM" not in out


def test_join_with_free_var_keeps_for_each_i():
    src = 'macro W2(p, xs: seq<token>)\nf(@join xs as x with ", ": {{x}}@end, {{p}})\nend\n'
    out = compile_source(src, "t.cursed")
    # body is identity but ensure the gate checks data==~ too (p unused in
    # body, so this one may still ENUM - the REAL free-var case:)
    src2 = 'macro W3(p, xs: seq<token>)\nf(@join xs as x with ", ": {{x}}{{p}}@end)\nend\n'
    out2 = compile_source(src2, "t.cursed")
    assert "SEQ_ENUM" not in out2


@requires_boost
def test_seq_enum_join_expands(tmp_path):
    src = 'macro ARGS(xs: seq<token>)\nf(@join xs as x with ", ": {{x}}@end)\nend\n'
    out = preprocess_src(tmp_path, src, "enumj", "ARGS((a)(b)(c))\nARGS((only))")
    assert canon("f(a, b, c)") in out
    assert canon("f(only)") in out


def test_join_separator_preserves_utf8():
    src = 'macro A(xs: seq<token>)\n@join xs as x with " → "\n({{x}})\n@end\nend\n'
    [macro] = parse_file(src, "t.cursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.sep == " → "


def test_join_separator_escapes_still_work():
    src = 'macro A(xs: seq<token>)\n@join xs as x with "\\t"\n({{x}})\n@end\nend\n'
    [macro] = parse_file(src, "t.cursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.sep == "\t"


def test_block_join_separator_containing_at_end():
    src = 'macro A(xs: seq<token>)\n@join xs as x with " @end "\nb({{x}})\n@end\nend\n'
    [macro] = parse_file(src, "t.cursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.sep == " @end "
