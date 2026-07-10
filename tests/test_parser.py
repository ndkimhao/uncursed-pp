import pytest

from cursedpp.nodes import (
    Concat,
    ElemAccess,
    ForEach,
    Interp,
    Join,
    Let,
    RemoveParens,
    SeqT,
    Text,
    TupleT,
    VarRef,
)
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


def test_inline_join_missing_end_is_error():
    src = 'macro P(xs: seq<token>)\nf(@join xs with ", ": {{xs}});\nend\n'
    with pytest.raises(CursedppError) as excinfo:
        parse_file(src, "t.cursed")
    assert "t.cursed:2" in str(excinfo.value)
