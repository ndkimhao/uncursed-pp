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


def test_generated_header_embeds_cursed_source():
    src = "# doubles x\nmacro TWICE(x)\n({{x}} + {{x}})\nend\n"
    out = compile_source(src, "t.cursed")
    comment = (
        "/* cursedpp source:\n"
        " * # doubles x\n"
        " * macro TWICE(x)\n"
        " * ({{x}} + {{x}})\n"
        " * end\n"
        " */\n"
    )
    assert comment in out
    assert out.index(comment) < out.index("#define TWICE(x)")


def test_source_comment_per_macro_with_own_comments():
    src = (
        "# first\nmacro A(x)\n{{x}}\nend\n"
        "\n"
        "# second\nmacro B(y)\n{{y}}\nend\n"
    )
    out = compile_source(src, "t.cursed")
    assert " * # first\n * macro A(x)\n" in out
    assert " * # second\n * macro B(y)\n" in out
    # each macro's comment sits with its own block
    assert out.index("# first") < out.index("#define A(x)") < out.index("# second")


def test_spec_comments_are_not_attached():
    src = "#? A(q)\n#=> q\n# real comment\nmacro A(x)\n{{x}}\nend\n"
    out = compile_source(src, "t.cursed")
    assert " * # real comment\n" in out
    assert "#?" not in out
    assert "#=>" not in out


def test_blank_line_detaches_comments():
    src = "# stale note\n\nmacro A(x)\n{{x}}\nend\n"
    out = compile_source(src, "t.cursed")
    assert "stale note" not in out


def test_comment_terminator_in_body_is_sanitized():
    src = "macro C(x)\n{{x}} /* inline */\nend\n"
    out = compile_source(src, "t.cursed")
    # the embedded source must not close the enclosing C comment early
    assert " * {{x}} /* inline * /\n" in out


def test_string_literal_whitespace_survives_emission():
    src = 'macro P(x)\nprintf("  a  b", {{x}});\nend\n'
    out = compile_source(src, "t.cursed")
    # check the #define itself, not the embedded source comment
    assert '#define P(x) printf("  a  b", x);' in out


def test_raw_string_interior_survives_emission():
    src = 'macro R1(x)\nconst char *s = R"(a " {{x}}  b " c)";\nend\n'
    out = compile_source(src, "t.cursed")
    # interior spacing of the raw string (even around embedded quotes)
    # must reach the #define untouched
    assert 'R"(a " x  b " c)"' in out.split("/* cursedpp source:")[0] or (
        'R"(a " ' in out and '  b " c)"' in out.rsplit("*/", 1)[-1]
    )


# ── adjacency never pastes implicitly; concat() is the explicit paste ──


def test_text_adjacent_to_interp_stays_separate_tokens():
    out = compile_source("macro P(x)\npre{{x}}post {{x}}5;\nend\n", "t.cursed")
    assert "#define P(x) pre x post x 5;\n" in out


def test_interp_adjacent_to_interp_stays_separate():
    out = compile_source("macro Q(a, b)\n{{a}}{{b}};\nend\n", "t.cursed")
    assert "#define Q(a, b) a b;\n" in out


def test_spread_tuple_field_adjacency_does_not_paste():
    src = "macro G(f: tuple<t, n>)\nget_{{f.n}} = {{f.t}}{{f.n}};\nend\n"
    out = compile_source(src, "t.cursed")
    assert "get_ n = t n;" in out


def test_punctuation_adjacency_stays_tight():
    out = compile_source("macro R(x)\n[{{x}}]({{x}});\nend\n", "t.cursed")
    assert "#define R(x) [x](x);\n" in out
