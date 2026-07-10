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


# ── spec-format strictness ───────────────────────────────────────────


def _specs(text):
    from test_e2e_specs import parse_specs

    return parse_specs(text)


def test_parse_specs_rejects_empty_invocation():
    import pytest

    with pytest.raises(ValueError):
        _specs("#?\n#=> x\n")


def test_parse_specs_rejects_empty_expectation_line():
    import pytest

    with pytest.raises(ValueError):
        _specs("#? F(1)\n#=>\n")


def test_parse_specs_supports_expected_failure_cases():
    cases = _specs("#?! F(bad)\n#? F(1)\n#=> ok\n")
    assert cases[0] == ("F(bad)", [], True)
    assert cases[1] == ("F(1)", ["ok"], False)


def test_expected_failure_cases_may_not_have_expectations():
    import pytest

    with pytest.raises(ValueError):
        _specs("#?! F(bad)\n#=> nothing\n")


def test_run_cpp_kills_runaway_preprocessing_cleanly(tmp_path):
    """A pathological TU must fail with a clean error, not OOM the host:
    run_cpp caps cpp's address space and wall time."""
    import pytest

    from conftest import CC, run_cpp

    if CC is None:
        pytest.skip("needs cc")
    bomb = "#define X0 x x\n"
    bomb += "".join(f"#define X{i} X{i-1} X{i-1} X{i-1} X{i-1}\n" for i in range(1, 24))
    bomb += "X23\n"
    (tmp_path / "bomb.c").write_text(bomb)
    with pytest.raises(AssertionError) as excinfo:
        run_cpp(tmp_path / "bomb.c")
    assert "preprocessing failed" in str(excinfo.value) or "timed out" in str(excinfo.value)

