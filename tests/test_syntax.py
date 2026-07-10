"""File structure: macro blocks, raw body text, {{interp}}, positions, errors."""

import pytest

from uncursed_pp.emitter import compile_source
from uncursed_pp.nodes import SeqT, Text, TupleT, VarRef
from uncursed_pp.parser import UncursedPpError, parse_file

DECLARE_FIELDS = """\
# a comment
@macro DECLARE_FIELDS(fields: seq<tuple<type, name>>)
@for (type, name) in fields
  {{type}} {{name}};
@end
@endmacro
"""


def test_parse_macro_signature():
    file = parse_file(DECLARE_FIELDS, "test.uncursed")
    [macro] = file.macros
    assert macro.name == "DECLARE_FIELDS"
    [param] = macro.params
    assert param.name == "fields"
    assert param.type == SeqT(TupleT(("type", "name")))


def test_parse_plain_macro_text_and_interp():
    from uncursed_pp.nodes import Interp

    file = parse_file("@macro ID(x)\nvalue: {{x}}!\n@endmacro\n", "t.uncursed")
    [macro] = file.macros
    assert macro.params[0].type is None  # bare token param
    lead, interp, tail = macro.body
    assert isinstance(lead, Text) and isinstance(interp, Interp) and isinstance(tail, Text)
    assert lead.value == "value: "
    assert interp.expr == VarRef("x")
    assert tail.value == "!\n"


def test_missing_end_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro FOO(x)\n{{x}}\n", "t.uncursed")
    assert "t.uncursed" in str(excinfo.value)


def test_unbalanced_at_end_is_error():
    src = "@macro FOO(xs: seq<token>)\n@end\n@endmacro\n"
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file(src, "t.uncursed")
    assert "t.uncursed:2" in str(excinfo.value)


def test_bad_signature_reports_position():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro FOO(x::)\nbody\n@endmacro\n", "t.uncursed")
    assert "t.uncursed:1" in str(excinfo.value)


def test_unexpected_toplevel_line_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("int stray;\n", "t.uncursed")
    assert "t.uncursed:1" in str(excinfo.value)


def test_plain_macro_single_line():
    out = compile_source("@macro ID(x)\n{{x}}\n@endmacro\n", "id.uncursed")
    assert "#define ID(x) x\n" in out
    assert "#pragma once" in out


def test_multiline_body_uses_continuations():
    src = "@macro TWO(a, b)\nfirst {{a}}\nsecond {{b}}\n@endmacro\n"
    out = compile_source(src, "two.uncursed")
    assert "#define TWO(a, b) \\\n    first a \\\n    second b\n" in out


def test_multiple_macros_share_one_header():
    src = "@macro A(x)\n{{x}}\n@endmacro\n@macro B(y)\n{{y}}\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "#define A(x) x\n" in out
    assert "#define B(y) y\n" in out
    assert out.count("#pragma once") == 1


def test_duplicate_interp_on_one_line():
    out = compile_source("@macro D(x)\n{{x}} + {{x}} + {{x}}\n@endmacro\n", "t.uncursed")
    assert "#define D(x) x + x + x\n" in out


def test_generated_header_embeds_uncursed_source():
    src = "# doubles x\n@macro TWICE(x)\n({{x}} + {{x}})\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    comment = (
        "/* uncursed-pp source:\n"
        " * # doubles x\n"
        " * @macro TWICE(x)\n"
        " * ({{x}} + {{x}})\n"
        " * @endmacro\n"
        " */\n"
    )
    assert comment in out
    assert out.index(comment) < out.index("#define TWICE(x)")


def test_source_comment_per_macro_with_own_comments():
    src = (
        "# first\n@macro A(x)\n{{x}}\n@endmacro\n"
        "\n"
        "# second\n@macro B(y)\n{{y}}\n@endmacro\n"
    )
    out = compile_source(src, "t.uncursed")
    assert " * # first\n * @macro A(x)\n" in out
    assert " * # second\n * @macro B(y)\n" in out
    # each macro's comment sits with its own block
    assert out.index("# first") < out.index("#define A(x)") < out.index("# second")


def test_spec_comments_are_not_attached():
    src = "#? A(q)\n#=> q\n# real comment\n@macro A(x)\n{{x}}\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert " * # real comment\n" in out
    # spec lines never join the macro's source block; they stand alone
    source_block = out.split("/* uncursed-pp source:")[1].split("*/")[0]
    assert "#?" not in source_block and "#=>" not in source_block
    assert "/* #? A(q)\n * #=> q\n */" in out


def test_blank_line_detaches_comments():
    # blank-detached comments do not join the macro's source block, but
    # they ARE preserved as a standalone comment at their own position
    src = "# stale note\n\n@macro A(x)\n{{x}}\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    source_block = out.split("/* uncursed-pp source:")[1].split("*/")[0]
    assert "stale note" not in source_block
    assert "/* # stale note */" in out


def test_comment_terminator_in_body_is_sanitized():
    src = "@macro C(x)\n{{x}} /* inline */\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    # the embedded source must not close the enclosing C comment early
    assert " * {{x}} /* inline * /\n" in out


def test_string_literal_whitespace_survives_emission():
    src = '@macro P(x)\nprintf("  a  b", {{x}});\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    # check the #define itself, not the embedded source comment
    assert '#define P(x) printf("  a  b", x);' in out


def test_raw_string_interior_survives_emission():
    src = '@macro R1(x)\nconst char *s = R"(a " {{x}}  b " c)";\n@endmacro\n'
    out = compile_source(src, "t.uncursed")
    # interior spacing of the raw string (even around embedded quotes)
    # must reach the #define untouched
    assert 'R"(a " x  b " c)"' in out.split("/* uncursed-pp source:")[0] or (
        'R"(a " ' in out and '  b " c)"' in out.rsplit("*/", 1)[-1]
    )


# ── adjacency never pastes implicitly; concat() is the explicit paste ──


def test_text_adjacent_to_interp_stays_separate_tokens():
    out = compile_source("@macro P(x)\npre{{x}}post {{x}}5;\n@endmacro\n", "t.uncursed")
    assert "#define P(x) pre x post x 5;\n" in out


def test_interp_adjacent_to_interp_stays_separate():
    out = compile_source("@macro Q(a, b)\n{{a}}{{b}};\n@endmacro\n", "t.uncursed")
    assert "#define Q(a, b) a b;\n" in out


def test_spread_tuple_field_adjacency_does_not_paste():
    src = "@macro G(f: tuple<t, n>)\nget_{{f.n}} = {{f.t}}{{f.n}};\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "get_ n = t n;" in out


def test_punctuation_adjacency_stays_tight():
    out = compile_source("@macro R(x)\n[{{x}}]({{x}});\n@endmacro\n", "t.uncursed")
    assert "#define R(x) [x](x);\n" in out


# ── template mistakes error instead of leaking into output ──────────


def test_unknown_at_directive_line_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro M(xs: seq<token>)\n@fro x in xs\nx\n@end\n@endmacro\n", "t.uncursed")
    assert "unknown directive" in str(excinfo.value)
    assert "t.uncursed:2" in str(excinfo.value)


def test_pragma_inside_macro_body_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro M(x)\n@pragma pp_prefix F_\n{{x}}\n@endmacro\n", "t.uncursed")
    assert "top level" in str(excinfo.value)


def test_unclosed_interpolation_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro M(x)\nvalue = {{x} + 1;\n@endmacro\n", "t.uncursed")
    assert "{{" in str(excinfo.value)
    assert "t.uncursed:2" in str(excinfo.value)


# ── @macro / @endmacro ───────────────────────────────────────────────


def test_atmacro_endmacro_parses():
    [macro] = parse_file("@macro ID(x)\n{{x}}\n@endmacro\n", "t.uncursed").macros
    assert macro.name == "ID"
    assert [p.name for p in macro.params] == ["x"]


def test_multiline_params_with_trailing_comma():
    src = (
        "@macro W(\n"
        "    name,\n"
        "    named WIDTH = 100,\n"
        "    named HEIGHT = 50,\n"
        ")\n"
        "w\n"
        "@endmacro\n"
    )
    [macro] = parse_file(src, "t.uncursed").macros
    assert [p.name for p in macro.params] == ["name", "WIDTH", "HEIGHT"]
    assert macro.params[1].named and macro.params[1].default == "100"


def test_bare_end_is_body_text():
    # 'end' alone on a line is ordinary C text now, not a terminator
    src = "@macro E(x)\nbegin\n" + "end" + "\n{{x}}\n@endmacro\n"
    [macro] = parse_file(src, "t.uncursed").macros
    text = "".join(n.value for n in macro.body if isinstance(n, Text))
    assert "end" in text


def test_multiline_params_without_trailing_comma():
    src = (
        "@macro F(\n"
        "    a,\n"
        "    xs: seq<token>\n"
        ")\n"
        "{{a}}\n"
        "@endmacro\n"
    )
    [macro] = parse_file(src, "t.uncursed").macros
    assert [p.name for p in macro.params] == ["a", "xs"]


def test_unclosed_param_list_is_an_error():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro F(a,\nb\n", "t.uncursed")
    assert "parameter list" in str(excinfo.value)


def test_endmacro_with_open_block_errors():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file(
            "@macro F(xs: seq<token>)\n@for x in xs\n{{x}}\n@endmacro\n", "t.uncursed"
        )
    assert "unclosed @for" in str(excinfo.value)


def test_missing_endmacro():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro F(x)\n{{x}}\n", "t.uncursed")
    assert "@endmacro" in str(excinfo.value)


# ── top-level comments are preserved in the generated header ─────────


def test_top_banner_comment_is_preserved():
    src = "# file banner\n# second line\n\n@macro A(x)\n{{x}}\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "/* # file banner\n * # second line\n */" in out
    assert out.index("# file banner") < out.index("#define A(x)")


def test_mid_file_comment_lands_between_macros():
    src = (
        "@macro A(x)\n{{x}}\n@endmacro\n"
        "\n# section two\n\n"
        "@macro B(y)\n{{y}}\n@endmacro\n"
    )
    out = compile_source(src, "t.uncursed")
    assert "/* # section two */" in out
    assert out.index("#define A(x)") < out.index("# section two") < out.index("#define B(y)")


def test_eof_comments_and_specs_are_preserved():
    src = "@macro A(x)\n{{x}}\n@endmacro\n\n#?  A(1)\n#=>     1\n\n# closing note\n"
    out = compile_source(src, "t.uncursed")
    assert "/* #?  A(1)\n * #=>     1\n */" in out
    assert "/* # closing note */" in out
    assert out.index("#define A(x)") < out.index("#?  A(1)")


def test_attached_comment_is_not_duplicated():
    src = "# doc\n@macro A(x)\n{{x}}\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert out.count("# doc") == 1  # lives in the macro's source block only
