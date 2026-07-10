from pathlib import Path

import pytest

from cursedpp.emitter import compile_source

GOLDEN = Path(__file__).parent / "golden"


def golden_cases() -> list[str]:
    return sorted(p.stem for p in GOLDEN.glob("*.cursed"))


@pytest.mark.parametrize("name", golden_cases())
def test_golden(name):
    source = (GOLDEN / f"{name}.cursed").read_text()
    expected = (GOLDEN / f"{name}.h").read_text()
    assert compile_source(source, f"{name}.cursed") == expected


def test_plain_macro_single_line():
    out = compile_source("macro ID(x)\n{{x}}\nend\n", "id.cursed")
    assert "#define ID(x) x\n" in out
    assert "#pragma once" in out


def test_multiline_body_uses_continuations():
    src = "macro TWO(a, b)\nfirst {{a}}\nsecond {{b}}\nend\n"
    out = compile_source(src, "two.cursed")
    assert "#define TWO(a, b) \\\n    first a \\\n    second b\n" in out


def test_seq_index_access():
    out = compile_source("macro F2(xs: seq<token>)\n{{xs[0]}}, {{xs[1]}}\nend\n", "f.cursed")
    assert "#define F2(xs) BOOST_PP_SEQ_ELEM(0, xs), BOOST_PP_SEQ_ELEM(1, xs)\n" in out


def test_join_with_non_comma_separator():
    src = 'macro ORS(xs: seq<token>)\n@join xs as x with " || "\n({{x}})\n@end\nend\n'
    out = compile_source(src, "ors.cursed")
    assert "#define CURSEDPP_ORS_SEP1() ||\n" in out
    assert (
        "#define CURSEDPP_ORS_EACH1(r, d, i, e) "
        "BOOST_PP_IF(i, CURSEDPP_ORS_SEP1, BOOST_PP_EMPTY)() (e)\n" in out
    )
    assert "#define ORS(xs) BOOST_PP_SEQ_FOR_EACH_I(CURSEDPP_ORS_EACH1, ~, xs)\n" in out


def test_undefined_variable_is_error():
    from cursedpp.parser import CursedppError

    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(x)\n{{y}}\nend\n", "f.cursed")
    assert "f.cursed:2" in str(excinfo.value)
    assert "y" in str(excinfo.value)


def test_concat_multiple_args_nests_cat():
    out = compile_source("macro F(a)\n{{concat(pre_, a, _post)}}\nend\n", "f.cursed")
    assert "#define F(a) BOOST_PP_CAT(pre_, BOOST_PP_CAT(a, _post))\n" in out
