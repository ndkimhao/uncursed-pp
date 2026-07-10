"""File structure: macro blocks, raw body text, {{interp}}, positions, errors."""

import pytest

from cursedpp.emitter import compile_source
from cursedpp.nodes import SeqT, Text, TupleT, VarRef
from cursedpp.parser import CursedppError, parse_file

DECLARE_FIELDS = """\
# a comment
macro DECLARE_FIELDS(fields: seq<tuple<type, name>>)
@for (type, name) in fields
  {{type}} {{name}};
@end
end
"""


def test_parse_macro_signature():
    file = parse_file(DECLARE_FIELDS, "test.cursed")
    [macro] = file.macros
    assert macro.name == "DECLARE_FIELDS"
    [param] = macro.params
    assert param.name == "fields"
    assert param.type == SeqT(TupleT(("type", "name")))


def test_parse_plain_macro_text_and_interp():
    file = parse_file("macro ID(x)\nvalue: {{x}}!\nend\n", "t.cursed")
    [macro] = file.macros
    assert macro.params[0].type is None  # bare token param
    kinds = [type(n) for n in macro.body]
    assert kinds == [Text, type(macro.body[1]), Text]
    assert macro.body[0].value == "value: "
    assert macro.body[1].expr == VarRef("x")
    assert macro.body[2].value == "!\n"


def test_missing_end_is_error():
    with pytest.raises(CursedppError) as excinfo:
        parse_file("macro FOO(x)\n{{x}}\n", "t.cursed")
    assert "t.cursed" in str(excinfo.value)


def test_unbalanced_at_end_is_error():
    src = "macro FOO(xs: seq<token>)\n@end\nend\n"
    with pytest.raises(CursedppError) as excinfo:
        parse_file(src, "t.cursed")
    assert "t.cursed:2" in str(excinfo.value)


def test_bad_signature_reports_position():
    with pytest.raises(CursedppError) as excinfo:
        parse_file("macro FOO(x::)\nbody\nend\n", "t.cursed")
    assert "t.cursed:1" in str(excinfo.value)


def test_unexpected_toplevel_line_is_error():
    with pytest.raises(CursedppError) as excinfo:
        parse_file("int stray;\n", "t.cursed")
    assert "t.cursed:1" in str(excinfo.value)


def test_plain_macro_single_line():
    out = compile_source("macro ID(x)\n{{x}}\nend\n", "id.cursed")
    assert "#define ID(x) x\n" in out
    assert "#pragma once" in out


def test_multiline_body_uses_continuations():
    src = "macro TWO(a, b)\nfirst {{a}}\nsecond {{b}}\nend\n"
    out = compile_source(src, "two.cursed")
    assert "#define TWO(a, b) \\\n    first a \\\n    second b\n" in out


def test_multiple_macros_share_one_header():
    src = "macro A(x)\n{{x}}\nend\nmacro B(y)\n{{y}}\nend\n"
    out = compile_source(src, "t.cursed")
    assert "#define A(x) x\n" in out
    assert "#define B(y) y\n" in out
    assert out.count("#pragma once") == 1


def test_duplicate_interp_on_one_line():
    out = compile_source("macro D(x)\n{{x}} + {{x}} + {{x}}\nend\n", "t.cursed")
    assert "#define D(x) x + x + x\n" in out
