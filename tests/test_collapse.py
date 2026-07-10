"""Helper dedup/factoring: identical helpers merge; single-token differences
parameterize through FOR_EACH's spare `d` argument; anything fancier stays
unmerged (generated-code simplicity wins)."""

from conftest import canon, preprocess_src, requires_boost
from uncursed_pp.emitter import compile_source

TWO_IDENTICAL = (
    "macro CALL_A(xs: seq<token>)\n@for x in xs\nf({{x}});\n@end\nend\n"
    "macro CALL_B(ys: seq<token>)\n@for y in ys\nf({{y}});\n@end\nend\n"
)

ONE_TOKEN_DIFF = (
    "macro DECLARE_INTS(xs: seq<token>)\n@for x in xs\nint {{x}};\n@end\nend\n"
    "macro DECLARE_FLOATS(ys: seq<token>)\n@for y in ys\nfloat {{y}};\n@end\nend\n"
)

TWO_TOKEN_DIFF = (
    "macro A(xs: seq<token>)\n@for x in xs\nint {{x}} = 0;\n@end\nend\n"
    "macro B(ys: seq<token>)\n@for y in ys\nfloat {{y}} = 1;\n@end\nend\n"
)


def test_exact_duplicate_helpers_collapse():
    out = compile_source(TWO_IDENTICAL, "t.uncursed")
    assert out.count("#define UNCURSED_PP_T_H1(r, d, e) f(e);") == 1
    assert "BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_T_H1, ~, xs)" in out
    assert "BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_T_H1, ~, ys)" in out
    assert "UNCURSED_PP_CALL_A_EACH1" not in out
    assert "UNCURSED_PP_CALL_B_EACH1" not in out


def test_single_token_diff_parameterizes_via_d():
    out = compile_source(ONE_TOKEN_DIFF, "t.uncursed")
    assert "#define UNCURSED_PP_T_H1(r, d, e) d e;" in out
    assert "BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_T_H1, int, xs)" in out
    assert "BOOST_PP_SEQ_FOR_EACH(UNCURSED_PP_T_H1, float, ys)" in out


def test_multi_token_diff_stays_unmerged():
    out = compile_source(TWO_TOKEN_DIFF, "t.uncursed")
    assert "_H1" not in out
    assert "#define UNCURSED_PP_A_EACH1(r, d, e) int e = 0;" in out
    assert "#define UNCURSED_PP_B_EACH1(r, d, e) float e = 1;" in out


def test_single_user_keeps_macro_specific_name():
    src = "macro ONLY(xs: seq<token>)\n@for x in xs\ng({{x}});\n@end\nend\n"
    out = compile_source(src, "t.uncursed")
    assert "UNCURSED_PP_ONLY_EACH1" in out
    assert "_H1" not in out


@requires_boost
def test_collapsed_shared_helper_expands_correctly(tmp_path):
    out = preprocess_src(
        tmp_path, ONE_TOKEN_DIFF, "coll", "DECLARE_INTS((a)(b))\nDECLARE_FLOATS((u)(v))"
    )
    assert canon("int a; int b;") in out
    assert canon("float u; float v;") in out


PUNCT_DIFF = (
    "macro STMTS(xs: seq<token>)\n@for x in xs\n{{x}};\n@end\nend\n"
    "macro LBLS(ys: seq<token>)\n@for y in ys\n{{y}}:\n@end\nend\n"
)

LITERAL_DIFF = (
    'macro TBL1(xs: seq<token>)\n@for x in xs\n{ {{x}}, "alpha" },\n@end\nend\n'
    'macro TBL2(ys: seq<token>)\n@for y in ys\n{ {{y}}, "beta" },\n@end\nend\n'
)


def test_punctuation_hole_does_not_paste_into_neighbor():
    out = compile_source(PUNCT_DIFF, "t.uncursed")
    import re

    assert not re.search(r"\bed\b", out)


def test_string_literal_hole_keeps_literal_whole():
    out = compile_source(LITERAL_DIFF, "t.uncursed")
    # a merge is fine only if the WHOLE literal travels through d;
    # a "d" spliced inside quotes can never substitute
    assert '"d"' not in out


@requires_boost
def test_collapse_merges_expand_correctly(tmp_path):
    out = preprocess_src(tmp_path, PUNCT_DIFF, "punct", "STMTS((a)(b))\nLBLS((done))")
    assert canon("a; b;") in out and canon("done:") in out
    out = preprocess_src(tmp_path, LITERAL_DIFF, "lit", "TBL1((k1))\nTBL2((k2))")
    assert canon('{ k1, "alpha" },') in out
    assert canon('{ k2, "beta" },') in out


def test_shared_helpers_are_namespaced_per_file():
    # two independently generated headers in one translation unit must not
    # collide on shared helper names
    out_a = compile_source(TWO_IDENTICAL, "widgets.uncursed")
    out_b = compile_source(
        "macro OTHER(xs: seq<token>)\n@for x in xs\ng({{x}})\n@end\nend\n"
        "macro OTHER2(ys: seq<token>)\n@for y in ys\ng({{y}})\n@end\nend\n",
        "gadgets.uncursed",
    )
    assert "UNCURSED_PP_WIDGETS_H1" in out_a
    assert "UNCURSED_PP_GADGETS_H1" in out_b
    import re

    assert not re.search(r"\bUNCURSED_PP_H1\b", out_a + out_b)
