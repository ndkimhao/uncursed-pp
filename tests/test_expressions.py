"""Expressions and @let: parsing and error paths.

Codegen shape and runtime behavior live in tests/golden/expressions/.
"""

import pytest

from cursedpp.emitter import compile_source
from cursedpp.nodes import (
    Concat,
    ElemAccess,
    Interp,
    Let,
    RemoveParens,
    Stringize,
    VarRef,
)
from cursedpp.parser import CursedppError, parse_file


def test_parse_let_and_concat():
    src = "macro G(f: tuple<t, n>)\n@let g := concat(get_, f.n)\n{{g}}\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    let = next(n for n in macro.body if isinstance(n, Let))
    assert let.name == "g"
    assert let.expr == Concat((VarRef("get_"), ElemAccess(VarRef("f"), "n")))


def test_parse_index_access_and_remove_parens():
    src = "macro F(xs: seq<token>)\n{{remove_parens(xs[0])}}\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    interp = next(n for n in macro.body if isinstance(n, Interp))
    assert interp.expr == RemoveParens(ElemAccess(VarRef("xs"), 0))


def test_parse_stringize():
    src = "macro F(x)\n{{stringize(x)}}\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    interp = next(n for n in macro.body if isinstance(n, Interp))
    assert interp.expr == Stringize(VarRef("x"))


def test_unknown_function_is_error():
    with pytest.raises(CursedppError) as excinfo:
        parse_file("macro F(x)\n{{mangle(x)}}\nend\n", "t.cursed")
    assert "unknown function" in str(excinfo.value)


def test_function_arity_errors():
    for bad in ["concat(a)", "remove_parens(a, b)", "stringize()", "len(a, b)"]:
        with pytest.raises(CursedppError):
            parse_file(f"macro F(a, b)\n{{{{{bad}}}}}\nend\n", "t.cursed")


def test_named_access_on_non_tuple_is_error():
    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(x)\n{{x.field}}\nend\n", "t.cursed")
    assert "tuple" in str(excinfo.value)


def test_unknown_tuple_field_is_error():
    src = "macro F(p: tuple<a, b>)\n{{p.c}}\nend\n"
    with pytest.raises(CursedppError) as excinfo:
        compile_source(src, "t.cursed")
    assert "no element 'c'" in str(excinfo.value)


def test_indexing_non_seq_is_error():
    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(x)\n{{x[0]}}\nend\n", "t.cursed")
    assert "seq" in str(excinfo.value)


def test_undefined_variable_is_error():
    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(x)\n{{y}}\nend\n", "f.cursed")
    assert "f.cursed:2" in str(excinfo.value)
    assert "y" in str(excinfo.value)


def test_let_is_block_scoped():
    src = (
        "macro F(xs: seq<token>)\n"
        "@for x in xs\n"
        "@let y := concat(x, _sfx)\n"
        "{{y}};\n"
        "@end\n"
        "{{y}}\n"
        "end\n"
    )
    with pytest.raises(CursedppError) as excinfo:
        compile_source(src, "t.cursed")
    assert "undefined variable: y" in str(excinfo.value)


def test_let_join_renders_one_helper_reused():
    src = (
        "macro CALL2(fn, args: seq<tuple<type, argname>>)\n"
        '@let joined := @join args with ", ": {{argname}}@end\n'
        "{{fn}}({{joined}}, {{joined}})\n"
        "end\n"
    )
    out = compile_source(src, "t.cursed")
    assert out.count("#define CURSEDPP_CALL2_EACH1") == 1
    assert out.count("BOOST_PP_SEQ_FOR_EACH_I(CURSEDPP_CALL2_EACH1, ~, args)") == 2


def test_let_rejects_mixed_inline_and_text():
    src = 'macro F(xs: seq<token>)\n@let j := prefix @join xs with ",": {{xs}}@end\n{{j}}\nend\n'
    with pytest.raises(CursedppError):
        parse_file(src, "t.cursed")
