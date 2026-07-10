"""The comparison function itself must be token-exact: whitespace between
tokens is insignificant in C, but INSIDE string/char literals it matters,
and identifier adjacency must never blur."""

from conftest import canon


def test_canon_ignores_whitespace_between_tokens():
    assert canon("f( x ,y ) ;") == canon("f(x, y);")
    assert canon("int  a ;\n int b;") == canon("int a; int b;")


def test_canon_preserves_string_literal_interiors():
    assert canon('"a  b"') != canon('"a b"')
    assert canon('printf("  " "x")') != canon('printf(" " "x")')


def test_canon_distinguishes_adjacent_identifiers_from_pasted():
    assert canon("get_ age") != canon("get_age")


def test_canon_handles_escaped_quotes():
    assert canon('"say \\"hi\\""  x') == canon('"say \\"hi\\"" x')
