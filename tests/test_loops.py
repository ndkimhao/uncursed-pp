"""@for and @join: parsing and error paths.

Codegen shape and runtime behavior live in tests/golden/loops/ (exact
header comparison + #? invocation specs) - not as string assertions here.
"""

import pytest

from conftest import canon, preprocess_src, requires_boost
from uncursed_pp.emitter import compile_source
from uncursed_pp.nodes import ForEach, Interp, Join, VarRef
from uncursed_pp.parser import UncursedPpError, parse_file


def test_parse_for_loop_body():
    src = (
        '@macro DECLARE_FIELDS($fields: seq<tuple<$type, $name>>)\n@for ($type, $name) in $fields\n  {{$type}} {{$name}};\n@end\n@endmacro\n'
    )
    [macro] = parse_file(src, "test.uncursed").macros
    [loop] = [n for n in macro.body if isinstance(n, ForEach)]
    assert loop.unpack == ("type", "name")
    assert loop.var is None
    assert loop.iterable == "fields"
    interps = [n for n in loop.body if isinstance(n, Interp)]
    assert [i.expr for i in interps] == [VarRef("type"), VarRef("name")]


def test_parse_inline_join():
    src = (
        '@macro PROTO($name, $args: seq<tuple<$type, $argname>>)\nvoid {{$name}}(@join $args with ", ": {{$type}} {{$argname}}@end);\n@endmacro\n'
    )
    [macro] = parse_file(src, "t.uncursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.iterable == "args"
    assert join.sep == ", "
    assert join.var is None
    interps = [n for n in join.body if isinstance(n, Interp)]
    assert [i.expr for i in interps] == [VarRef("type"), VarRef("argname")]


def test_parse_line_form_join_with_as_binding():
    src = '@macro ORS($xs: seq<token>)\n@join $xs as $x with " || "\n({{$x}})\n@end\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.var == "x"
    assert join.sep == " || "
    assert join.iterable == "xs"


def test_inline_join_missing_end_is_error():
    src = '@macro P($xs: seq<token>)\nf(@join $xs with ", ": {{$xs}});\n@endmacro\n'
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file(src, "t.uncursed")
    assert "t.uncursed:2" in str(excinfo.value)


def test_unclosed_line_form_loop_is_error():
    src = "@macro F(xs: seq<token>)\n@for x in xs\n{{x}}\n@endmacro\n"
    with pytest.raises(UncursedPpError):
        parse_file(src, "t.uncursed")


def test_iterating_non_seq_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source('@macro F($x)\n@for $a in $x\n{{$a}}\n@end\n@endmacro\n', "t.uncursed")
    assert "non-seq" in str(excinfo.value)


def test_iterating_undefined_name_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source('@macro F($x)\n@for $a in $nope\n{{$a}}\n@end\n@endmacro\n', "t.uncursed")
    assert "undefined" in str(excinfo.value)


def test_unpack_arity_mismatch_is_error():
    src = '@macro F($xs: seq<tuple<$a, $b>>)\n@for ($a, $b, $c) in $xs\n{{$a}}\n@end\n@endmacro\n'
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "arity" in str(excinfo.value)


def test_unpacking_non_tuple_seq_is_error():
    src = '@macro F($xs: seq<token>)\n@for ($a, $b) in $xs\n{{$a}}\n@end\n@endmacro\n'
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "tuple" in str(excinfo.value)


NESTED_FOR = (
    '@macro CROSS($xs: seq<token>, $ys: seq<token>)\n@for $x in $xs\n@for $y in $ys\npair({{$x}}, {{$y}});\n@end\n@end\n@endmacro\n'
)

LOOP_IF_LOOP = (
    '@macro DEEP($xs: seq<token>, $ys: seq<token>)\n@for $x in $xs\n@if len($ys) == 1\n@for $y in $ys\np({{$y}});\n@end\n@end\n@end\n@endmacro\n'
)

JOIN_IN_FOR = (
    '@macro JF($xs: seq<token>, $ys: seq<token>)\n@for $x in $xs\nf(@join $ys as $y with ", ": {{$y}}@end);\n@end\n@endmacro\n'
)


def test_nested_for_uses_repeat():
    out = compile_source(NESTED_FOR, "t.uncursed")
    # outer level keeps SEQ_FOR_EACH; the inner level iterates via the
    # auto-reentrant BOOST_PP_REPEAT with SEQ_ELEM element access
    assert "BOOST_PP_SEQ_FOR_EACH(" in out
    assert "BOOST_PP_REPEAT(BOOST_PP_SEQ_SIZE(" in out
    assert "(z, n, d)" in out


def test_loop_if_loop_compiles():
    out = compile_source(LOOP_IF_LOOP, "t.uncursed")
    assert "BOOST_PP_REPEAT(" in out


def test_inline_join_inside_for_compiles():
    out = compile_source(JOIN_IN_FOR, "t.uncursed")
    assert "BOOST_PP_REPEAT(" in out
    assert "BOOST_PP_COMMA_IF(n)" in out


def test_let_join_inside_loop_compiles():
    src = (
        '@macro F($xs: seq<token>, $ys: seq<token>)\n@for $x in $xs\n@let $j := @join $ys as $y with ", ": {{$y}}@end\ng({{$j}});\n@end\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")
    assert "BOOST_PP_REPEAT(" in out


def test_loops_nest_at_most_four_deep():
    inner = "quint({{$a}}, {{$b}}, {{$c}}, {{$d2}}, {{$e2}});\n"
    src = "@macro F($s1: seq<token>, $s2: seq<token>, $s3: seq<token>, $s4: seq<token>, $s5: seq<token>)\n"
    for var, seq in [("a", "s1"), ("b", "s2"), ("c", "s3"), ("d2", "s4"), ("e2", "s5")]:
        src += f"@for ${var} in ${seq}\n"
    src += inner + "@end\n" * 5 + "@endmacro\n"
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "4 deep" in str(excinfo.value)


def test_sibling_loops_are_fine():
    src = (
        '@macro TWO($xs: seq<token>)\n@for $x in $xs\na({{$x}});\n@end\n@for $x in $xs\nb({{$x}});\n@end\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")
    # (the near-identical bodies collapse into one shared d-parameterized
    # helper - the point is both call sites exist and nothing was rejected)
    assert out.count("BOOST_PP_SEQ_FOR_EACH(") == 2


def test_loop_inside_if_branch_is_fine():
    src = (
        '@macro OPT($xs: seq<token>)\n@if len($xs) == 1\nsolo({{$xs[0]}})\n@else\n@for $x in $xs\nmany({{$x}});\n@end\n@end\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")  # no loop encloses the @if
    assert "UNCURSED_PP_OPT_EACH1" in out

# ── AP unpacking: tuple elements become direct macro params ─────────


def test_tuple_loop_unpacks_via_ap_juxtaposition():
    src = '@macro DECL($fields: seq<tuple<$type, $name>>)\n@for ($type, $name) in $fields\n  {{$type}} {{$name}};\n@end\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_DECL_AP1(type, name) type name;\n" in out
    assert "#define UNCURSED_PP_DECL_EACH1(r, d, e) UNCURSED_PP_DECL_AP1 e\n" in out
    assert "TUPLE_ELEM" not in out


def test_tuple_loop_with_free_var_spreads_through_d():
    src = (
        '@macro TBL($sname, $fields: seq<tuple<$type, $name>>)\n@for ($type, $name) in $fields\n  { {{stringize($name)}}, offsetof({{$sname}}, {{$name}}) },\n@end\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")
    assert (
        "#define UNCURSED_PP_TBL_AP1(sname, type, name) "
        "{ BOOST_PP_STRINGIZE(name), offsetof(sname, name) },\n" in out
    )
    assert "#define UNCURSED_PP_TBL_AP1_D(...) UNCURSED_PP_TBL_AP1(__VA_ARGS__)\n" in out
    assert (
        "#define UNCURSED_PP_TBL_EACH1(r, d, e) UNCURSED_PP_TBL_AP1_D(d, UNCURSED_PP_KW_SPREAD e)\n"
        in out
    )
    assert "BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_TBL_EACH1, sname, fields)" in out
    assert '#include "uncursed_pp_runtime.h"' in out
    assert "TUPLE_ELEM" not in out


def test_as_binding_keeps_element_form():
    src = '@macro F($xs: seq<tuple<$a, $b>>)\n@for $x in $xs\ng({{$x}});\n@end\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_F_EACH1(r, d, e) g(e);\n" in out


def test_tuple_loop_with_two_free_vars_spreads_d_tuple():
    src = (
        '@macro T2($a, $b, $fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\n  f({{$a}}, {{$b}}, {{$t}}, {{$n}});\n@end\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_T2_AP1(a, b, t, n) f(a, b, t, n);\n" in out
    assert (
        "#define UNCURSED_PP_T2_EACH1(r, d, e) "
        "UNCURSED_PP_T2_AP1_D(UNCURSED_PP_KW_SPREAD d, UNCURSED_PP_KW_SPREAD e)\n" in out
    )
    assert "BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_T2_EACH1, (a, b), fields)" in out


def test_field_name_colliding_with_free_var_falls_back_to_tuple_elem():
    src = (
        '@macro C($type, $fields: seq<tuple<$type, $name>>)\n@for ($type, $name) in $fields\n  {{$type}} {{$name}};\n@end\n@endmacro\n'
    )
    # unpack name shadows the outer param; if the body ALSO used the outer
    # value it couldn't - here the shadowing unpack wins and AP still applies
    out = compile_source(src, "t.uncursed")
    assert "UNCURSED_PP_C_AP1(type, name)" in out


def test_conditional_inside_ap_loop_body():
    src = (
        '@macro P($fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\n  @if is_paren($t) {{remove_parens($t)}} {{$n}}; @else {{$t}} {{$n}}; @end\n@end\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")
    # branch helpers receive the AP params by name
    assert "#define UNCURSED_PP_P_THEN1(t, n) BOOST_PP_REMOVE_PARENS(t) n;\n" in out
    assert "UNCURSED_PP_P_THEN1, UNCURSED_PP_P_ELSE1)(t, n)" in out


@requires_boost
def test_ap_loop_with_conditional_expands(tmp_path):
    src = (
        '@macro P($fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\n  @if is_paren($t) {{remove_parens($t)}} {{$n}}; @else {{$t}} {{$n}}; @end\n@end\n@endmacro\n'
    )
    out = preprocess_src(tmp_path, src, "p", "P((((a, b), x))((int, y)))")
    assert canon("a, b x; int y;") in out


# ── identity comma joins compile to table-driven SEQ_ENUM ───────────


def test_identity_comma_join_uses_seq_enum():
    src = '@macro ARGS($xs: seq<token>)\nf(@join $xs as $x with ", ": {{$x}}@end)\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define ARGS(xs) f(BOOST_PP_SEQ_ENUM(xs))\n" in out
    assert "SEQ_FOR_EACH_I" not in out
    assert "#include <boost/preprocessor/seq/enum.hpp>" in out


def test_non_identity_comma_join_keeps_for_each_i():
    src = '@macro W($xs: seq<token>)\nf(@join $xs as $x with ", ": g({{$x}})@end)\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "SEQ_FOR_EACH_I" in out
    assert "SEQ_ENUM" not in out


def test_join_with_free_var_keeps_for_each_i():
    src = '@macro W2($p, $xs: seq<token>)\nf(@join $xs as $x with ", ": {{$x}}@end, {{$p}})\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    # body is identity but ensure the gate checks data==~ too (p unused in
    # body, so this one may still ENUM - the REAL free-var case:)
    src2 = '@macro W3($p, $xs: seq<token>)\nf(@join $xs as $x with ", ": {{$x}}{{$p}}@end)\n@endmacro\n'
    out2 = compile_source(src2, "t.uncursed")
    assert "SEQ_ENUM" not in out2


@requires_boost
def test_seq_enum_join_expands(tmp_path):
    src = '@macro ARGS($xs: seq<token>)\nf(@join $xs as $x with ", ": {{$x}}@end)\n@endmacro\n'
    out = preprocess_src(tmp_path, src, "enumj", "ARGS((a)(b)(c))\nARGS((only))")
    assert canon("f(a, b, c)") in out
    assert canon("f(only)") in out


def test_join_separator_preserves_utf8():
    src = '@macro A($xs: seq<token>)\n@join $xs as $x with " → "\n({{$x}})\n@end\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.sep == " → "


def test_join_separator_escapes_still_work():
    src = '@macro A($xs: seq<token>)\n@join $xs as $x with "\\t"\n({{$x}})\n@end\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.sep == "\t"


def test_block_join_separator_containing_at_end():
    src = '@macro A($xs: seq<token>)\n@join $xs as $x with " @end "\nb({{$x}})\n@end\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    join = next(n for n in macro.body if isinstance(n, Join))
    assert join.sep == " @end "

# ── chain iteration: SEQ_ENUM-style consumption chains for loops ────


def test_tuple_for_loop_compiles_to_chain():
    src = '@macro DECL2($fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\n  {{$t}} {{$n}};\n@end\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    # chain members: body inlined, continuation eats the next element
    assert "#define UNCURSED_PP_DECL2_CH1_1(e) UNCURSED_PP_DECL2_AP1 e\n" in out
    assert "#define UNCURSED_PP_DECL2_CH1_2(e) UNCURSED_PP_DECL2_AP1 e UNCURSED_PP_DECL2_CH1_1\n" in out
    assert "#define UNCURSED_PP_DECL2_CH1_16(e) UNCURSED_PP_DECL2_AP1 e UNCURSED_PP_DECL2_CH1_15\n" in out
    # size-class pick: table lookup chooses chain vs FOR_EACH fallback
    assert (
        "#define UNCURSED_PP_DECL2_PICK1(n) "
        "BOOST_PP_IIF(BOOST_PP_CAT(UNCURSED_PP_LE16_, n), UNCURSED_PP_DECL2_SMALL1, UNCURSED_PP_DECL2_BIG1)\n"
        in out
    )
    assert "#define UNCURSED_PP_DECL2_SMALL1(seq) BOOST_PP_CAT(UNCURSED_PP_DECL2_CH1_, BOOST_PP_SEQ_SIZE(seq)) seq\n" in out
    assert "BOOST_PP_SEQ_FOR_EACH" in out  # BIG fallback still present
    assert "UNCURSED_PP_DECL2_PICK1(BOOST_PP_SEQ_SIZE(fields))(fields)" in out
    assert '#include "uncursed_pp_runtime.h"' in out  # LE16 table lives there


def test_join_with_separator_chains_and_bakes_sep():
    src = '@macro CALLS($xs: seq<token>)\n@join $xs as $x with ", ": g({{$x}})@end\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_CALLS_CH1_1(e) g(e)\n" in out
    assert "#define UNCURSED_PP_CALLS_CH1_2(e) g(e), UNCURSED_PP_CALLS_CH1_1\n" in out


def test_identity_comma_join_still_prefers_seq_enum():
    src = '@macro ARGS2($xs: seq<token>)\nf(@join $xs as $x with ", ": {{$x}}@end)\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "SEQ_ENUM" in out
    assert "CH1_" not in out


def test_free_var_loop_keeps_for_each():
    src = '@macro TBL2($s, $fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\noffsetof({{$s}}, {{$n}});\n@end\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "CH1_" not in out
    assert "BOOST_PP_SEQ_FOR_EACH(" in out


def test_pragma_loop_chain_off():
    src = '@pragma loop_chain off\n@macro D($fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\n{{$t}} {{$n}};\n@end\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "CH1_" not in out


def test_pragma_loop_chain_limit_table_lives_in_runtime():
    src = '@pragma loop_chain_limit 4\n@macro D($fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\n{{$t}} {{$n}};\n@end\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_D_CH1_4(e)" in out
    assert "UNCURSED_PP_D_CH1_5" not in out
    # non-default K: the size-class table comes from the shared runtime
    assert '#include "uncursed_pp_runtime.h"' in out
    assert "UNCURSED_PP_LE4_" in out           # referenced by the dispatch...
    assert "#define UNCURSED_PP_LE4_4" not in out  # ...but not defined locally


def test_nested_loop_bodies_keep_existing_machinery():
    src = (
        '@macro N2($xss: seq<token>, $ys: seq<token>)\n@for $x in $xss\n@for $y in $ys\np({{$x}}, {{$y}});\n@end\n@end\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")
    assert "CH1_" not in out


@requires_boost
def test_chain_loop_expands_at_boundary_sizes(tmp_path):
    src = '@macro DECL2($fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\n  {{$t}} {{$n}};\n@end\n@endmacro\n'
    seq16 = "".join(f"((t{i}, f{i}))" for i in range(16))
    seq17 = "".join(f"((u{i}, g{i}))" for i in range(17))
    out = preprocess_src(
        tmp_path, src, "chainb", f"DECL2(((int, x)))\nDECL2({seq16})\nDECL2({seq17})"
    )
    assert canon("int x;") in out
    assert canon("t0 f0;") in out and canon("t15 f15;") in out
    assert canon("u0 g0;") in out and canon("u16 g16;") in out  # fallback path


def test_full_range_chain_limit_drops_dispatch_and_fallback():
    # at limit 256 no seq can exceed the chain (BOOST_PP_LIMIT_SEQ), so the
    # size-class pick, the FOR_EACH fallback, and its include all vanish
    src = (
        '@pragma loop_chain_limit 256\n@macro D($fields: seq<tuple<$t, $n>>)\n@for ($t, $n) in $fields\n{{$t}} {{$n}};\n@end\n@endmacro\n'
    )
    out = compile_source(src, "t.uncursed")
    assert "#define UNCURSED_PP_D_CH1_256(e)" in out
    assert "BOOST_PP_CAT(UNCURSED_PP_D_CH1_, BOOST_PP_SEQ_SIZE(fields)) fields" in out
    assert "PICK" not in out and "SMALL" not in out and "BIG" not in out
    assert "SEQ_FOR_EACH" not in out
    assert "seq/for_each.hpp" not in out
    assert "LE256" not in out  # no size table needed either
