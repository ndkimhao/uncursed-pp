import pytest

from cursedpp.nodes import ForEach, Interp, SeqT, Text, TupleT, VarRef
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


def test_parse_for_loop_body():
    file = parse_file(DECLARE_FIELDS, "test.cursed")
    [macro] = file.macros
    [loop] = [n for n in macro.body if isinstance(n, ForEach)]
    assert loop.unpack == ("type", "name")
    assert loop.var is None
    assert loop.iterable == "fields"
    interps = [n for n in loop.body if isinstance(n, Interp)]
    assert [i.expr for i in interps] == [VarRef("type"), VarRef("name")]


def test_parse_plain_macro_text_and_interp():
    file = parse_file("macro ID(x)\nvalue: {{x}}!\nend\n", "t.cursed")
    [macro] = file.macros
    assert macro.params[0].type is None  # bare token param
    kinds = [type(n) for n in macro.body]
    assert kinds == [Text, Interp, Text]
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
