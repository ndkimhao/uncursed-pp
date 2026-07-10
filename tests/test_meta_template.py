"""Jinja2 meta-templating: `.uncursed` sources pass through Jinja2 before
parsing, with alternative delimiters that cannot clash with the DSL:
statements `<<% %>>`, expressions `<<{ }>>`, comments `<<# #>>`.
Codegen shape and runtime behavior live in tests/golden/basics/meta.*"""

import pytest

from uncursed_pp.emitter import compile_source
from uncursed_pp.parser import UncursedPpError, parse_file
from uncursed_pp.meta import render_meta

MACRO = "@macro ID($x)\n{{$x}}\n@endmacro\n"


def test_expression_delimiters() -> None:
    assert render_meta("cap = <<{ 2 ** 4 }>>\n", "t.uncursed") == "cap = 16\n"


def test_statement_loop_generates_macros() -> None:
    src = (
        "<<% for n in ['ALPHA', 'BETA'] %>>\n"
        "@macro GET_<<{ n }>>($v)\n"
        "<<{ n.lower() }>>({{$v}})\n"
        "@endmacro\n"
        "<<% endfor %>>\n"
    )
    file = parse_file(render_meta(src, "t.uncursed"), "t.uncursed")
    assert [m.name for m in file.macros] == ["GET_ALPHA", "GET_BETA"]
    out = compile_source(src, "t.uncursed")
    assert "#define GET_ALPHA(v) alpha(v)" in out
    assert "#define GET_BETA(v) beta(v)" in out


def test_comments_vanish() -> None:
    src = "<<# generator note #>>" + MACRO
    assert render_meta(src, "t.uncursed") == MACRO


def test_plain_sources_pass_through_verbatim() -> None:
    # no meta markers -> byte-identical (Jinja2 is not even invoked)
    src = "# plain { { }} <% %> comment\n" + MACRO + "#?  ID(1)\n#=>     1\n"
    assert render_meta(src, "t.uncursed") is src


def test_dsl_interpolation_untouched() -> None:
    # {{$x}} is DSL syntax, not a Jinja2 expression
    src = "<<# meta #>>@macro F($x)\n{{$x}} <<{ 1 + 1 }>>\n@endmacro\n"
    assert "{{$x}} 2" in render_meta(src, "t.uncursed")


def test_meta_syntax_error_is_positioned() -> None:
    src = MACRO + "<<% if %>>\n"
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "t.uncursed:4" in str(excinfo.value)
    assert "jinja2" in str(excinfo.value)


def test_undefined_names_fail_loudly() -> None:
    src = "@macro F($x)\n{{$x}} <<{ nope }>>\n@endmacro\n"
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source(src, "t.uncursed")
    assert "nope" in str(excinfo.value)
