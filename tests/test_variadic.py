"""Variadic (...) parameters, untyped and typed.

Codegen shape and runtime behavior live in tests/golden/variadic/ and
the compose goldens (enum_system uses variadic<tuple<...>>).
"""

import pytest

from cursedpp.emitter import compile_source
from cursedpp.nodes import TupleT, VariadicT
from cursedpp.parser import CursedppError, parse_file


def test_parse_variadic_param():
    src = "macro F(prefix, items: variadic)\n{{prefix}}: {{items[0]}}\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    assert macro.params[1].type == VariadicT()


def test_parse_typed_variadic():
    src = "macro F(items: variadic<tuple<a, b>>)\n@for (a, b) in items\n{{a}} {{b}};\n@end\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    assert macro.params[0].type == VariadicT(TupleT(("a", "b")))


def test_variadic_body_sees_a_seq():
    src = "macro F(items: variadic)\n{{len(items)}}: {{items[0]}}\nend\n"
    out = compile_source(src, "t.cursed")
    assert "#define F(...)" in out
    assert "BOOST_PP_SEQ_SIZE(BOOST_PP_VARIADIC_TO_SEQ(__VA_ARGS__))" in out
    assert "BOOST_PP_SEQ_ELEM(0, BOOST_PP_VARIADIC_TO_SEQ(__VA_ARGS__))" in out


def test_variadic_must_be_last():
    with pytest.raises(CursedppError):
        compile_source("macro F(items: variadic, x)\n{{x}}\nend\n", "f.cursed")


def test_variadic_excludes_defaults():
    with pytest.raises(CursedppError):
        compile_source("macro F(a = 1, items: variadic)\n{{a}}\nend\n", "f.cursed")


def test_variadic_excludes_named():
    with pytest.raises(CursedppError):
        compile_source(
            "macro F(a, named B = 1, items: variadic)\n{{a}}\nend\n", "f.cursed"
        )


def test_at_most_one_variadic():
    with pytest.raises(CursedppError):
        compile_source(
            "macro F(a: variadic, b: variadic)\n{{a[0]}}\nend\n", "f.cursed"
        )
