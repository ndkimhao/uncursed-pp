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
