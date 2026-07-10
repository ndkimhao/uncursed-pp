"""Helper dedup/factoring: identical helpers merge; single-token differences
parameterize through FOR_EACH's spare `d` argument; anything fancier stays
unmerged (generated-code simplicity wins)."""

from conftest import canon, preprocess_src, requires_boost
from cursedpp.emitter import compile_source

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
    out = compile_source(TWO_IDENTICAL, "t.cursed")
    assert out.count("#define CURSEDPP_H1(r, d, e) f(e);") == 1
    assert "BOOST_PP_SEQ_FOR_EACH(CURSEDPP_H1, ~, xs)" in out
    assert "BOOST_PP_SEQ_FOR_EACH(CURSEDPP_H1, ~, ys)" in out
    assert "CURSEDPP_CALL_A_EACH1" not in out
    assert "CURSEDPP_CALL_B_EACH1" not in out


def test_single_token_diff_parameterizes_via_d():
    out = compile_source(ONE_TOKEN_DIFF, "t.cursed")
    assert "#define CURSEDPP_H1(r, d, e) d e;" in out
    assert "BOOST_PP_SEQ_FOR_EACH(CURSEDPP_H1, int, xs)" in out
    assert "BOOST_PP_SEQ_FOR_EACH(CURSEDPP_H1, float, ys)" in out


def test_multi_token_diff_stays_unmerged():
    out = compile_source(TWO_TOKEN_DIFF, "t.cursed")
    assert "CURSEDPP_H1" not in out
    assert "#define CURSEDPP_A_EACH1(r, d, e) int e = 0;" in out
    assert "#define CURSEDPP_B_EACH1(r, d, e) float e = 1;" in out


def test_single_user_keeps_macro_specific_name():
    src = "macro ONLY(xs: seq<token>)\n@for x in xs\ng({{x}});\n@end\nend\n"
    out = compile_source(src, "t.cursed")
    assert "CURSEDPP_ONLY_EACH1" in out
    assert "CURSEDPP_H1" not in out


@requires_boost
def test_collapsed_shared_helper_expands_correctly(tmp_path):
    out = preprocess_src(
        tmp_path, ONE_TOKEN_DIFF, "coll", "DECLARE_INTS((a)(b))\nDECLARE_FLOATS((u)(v))"
    )
    assert canon("int a; int b;") in out
    assert canon("float u; float v;") in out
