"""Variadic (...) parameters, untyped and typed.

Codegen shape and runtime behavior live in tests/golden/variadic/ and
the compose goldens (enum_system uses variadic<tuple<...>>).
"""

import pytest

from uncursed_pp.emitter import compile_source
from uncursed_pp.nodes import TupleT, VariadicT
from uncursed_pp.parser import UncursedPpError, parse_file


def test_parse_variadic_param():
    src = '@macro F($prefix, $items: variadic)\n{{$prefix}}: {{$items[0]}}\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    assert macro.params[1].type == VariadicT()


def test_parse_typed_variadic():
    src = '@macro F($items: variadic<tuple<$a, $b>>)\n@for ($a, $b) in $items\n{{$a}} {{$b}};\n@end\n@endmacro\n'
    [macro] = parse_file(src, "t.uncursed").macros
    assert macro.params[0].type == VariadicT(TupleT(("a", "b")))


def test_variadic_body_sees_a_seq():
    src = '@macro F($items: variadic)\n{{len($items)}}: {{$items[0]}}\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    assert "#define F(...)" in out
    assert "BOOST_PP_SEQ_SIZE(BOOST_PP_VARIADIC_TO_SEQ(__VA_ARGS__))" in out
    assert "BOOST_PP_SEQ_ELEM(0, BOOST_PP_VARIADIC_TO_SEQ(__VA_ARGS__))" in out


def test_variadic_must_be_last():
    with pytest.raises(UncursedPpError):
        compile_source('@macro F($items: variadic, $x)\n{{$x}}\n@endmacro\n', "f.uncursed")


def test_variadic_excludes_defaults():
    with pytest.raises(UncursedPpError):
        compile_source('@macro F($a = 1, $items: variadic)\n{{$a}}\n@endmacro\n', "f.uncursed")


def test_variadic_excludes_named():
    with pytest.raises(UncursedPpError):
        compile_source(
            '@macro F($a, named $B = 1, $items: variadic)\n{{$a}}\n@endmacro\n', "f.uncursed"
        )


def test_at_most_one_variadic():
    with pytest.raises(UncursedPpError):
        compile_source(
            '@macro F($a: variadic, $b: variadic)\n{{$a[0]}}\n@endmacro\n', "f.uncursed"
        )
