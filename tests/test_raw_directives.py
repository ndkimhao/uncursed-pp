"""`@#` raw preprocessing-directive passthrough: the rest of the line is
emitted VERBATIM (prefixed `#`) into the generated header at its source
position. Codegen shape and runtime behavior live in
tests/golden/basics/raw_directives.*"""

import pytest

from uncursed_pp.emitter import compile_source
from uncursed_pp.parser import UncursedPpError, parse_file

MACRO = "@macro ID($x)\n{{$x}}\n@endmacro\n"


def test_parse_directive_positions() -> None:
    src = "@#include <stdint.h>\n" + MACRO + "@#define TAIL 1\n"
    file = parse_file(src, "t.uncursed")
    assert file.directives == [(0, "#include <stdint.h>"), (1, "#define TAIL 1")]


def test_directives_are_emitted_raw_at_their_positions() -> None:
    src = "@#include <stdint.h>\n" + MACRO + "@#define TAIL 1\n"
    out = compile_source(src, "t.uncursed")
    assert "#include <stdint.h>\n" in out
    assert "#define TAIL 1\n" in out
    body = out.index("#define ID(")
    assert out.index("#include <stdint.h>") < body < out.index("#define TAIL 1")


def test_directive_text_is_verbatim() -> None:
    # no comment handling on @# lines: '#x', '##', and any other '#' all
    # pass through untouched
    src = "@#define STR(x) #x\n@#define GLUE(a, b) a ## b\n" + MACRO
    file = parse_file(src, "t.uncursed")
    assert file.directives == [
        (0, "#define STR(x) #x"),
        (0, "#define GLUE(a, b) a ## b"),
    ]


def test_line_continuation_belongs_to_the_directive() -> None:
    src = "@#define PAIR(a, b) \\\n    { (a), (b) }\n" + MACRO
    file = parse_file(src, "t.uncursed")
    assert file.directives == [(0, "#define PAIR(a, b) \\\n    { (a), (b) }")]


def test_multi_line_continuation() -> None:
    src = "@#define TRIPLE(a) \\\n    { (a), \\\n      (a), (a) }\n" + MACRO
    file = parse_file(src, "t.uncursed")
    [(_, text)] = file.directives
    assert text.count("\\\n") == 2
    assert text.endswith("(a), (a) }")


def test_continuation_at_eof_is_an_error() -> None:
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file(MACRO + "@#define X \\\n", "t.uncursed")
    assert "continuation" in str(excinfo.value)


def test_directive_inside_macro_body_is_an_error() -> None:
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@macro F($x)\n@#define NOPE 1\n{{$x}}\n@endmacro\n", "t.uncursed")
    assert "top level" in str(excinfo.value)


def test_empty_directive_is_an_error() -> None:
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@#\n" + MACRO, "t.uncursed")
    assert "@#" in str(excinfo.value)
