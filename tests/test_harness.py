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


def test_canon_raw_strings_with_embedded_quotes():
    # C++11 raw strings: content between the ( ) is verbatim, even quotes
    assert canon('R"(x " a  b " y)"') != canon('R"(x " a b " y)"')
    assert canon('R"xy(a  b)xy"') != canon('R"xy(a b)xy"')


def test_canon_distinguishes_prefixed_literal_from_ident_plus_string():
    # L"x" is ONE wide-string token; L "x" is an identifier then a string
    assert canon('L"x"') != canon('L "x"')
    assert canon('u8"x"') != canon('u8 "x"')
