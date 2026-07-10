"""The `uncursed-pp-eval` tool: expansion printing and `<???>` spec
filling. Plain templates need no Boost, so a bare C compiler suffices;
--format tests skip when clang-format is absent."""

import shutil

import pytest

from conftest import CC
from uncursed_pp.evaltool import main

pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler available")

TEMPLATE = "@macro PAIR($a, $b)\n{ {{$a}}, {{$b}} }\n@endmacro\n"


def _run(argv):
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    return excinfo.value.code


def test_prints_expansion(tmp_path, capsys):
    src = tmp_path / "t.uncursed"
    src.write_text(TEMPLATE)
    assert _run([str(src), "PAIR(1, 2)"]) == 0
    assert capsys.readouterr().out.strip() == "{ 1, 2 }"


def test_failed_preprocessing_exits_one(tmp_path, capsys):
    src = tmp_path / "t.uncursed"
    src.write_text(TEMPLATE)
    assert _run([str(src), "PAIR(1"]) == 1
    assert "preprocessing failed" in capsys.readouterr().err


def test_requires_invocation_or_update_specs(tmp_path, capsys):
    src = tmp_path / "t.uncursed"
    src.write_text(TEMPLATE)
    assert _run([str(src)]) == 2
    assert _run([str(src), "PAIR(1, 2)", "--update-specs"]) == 2


def test_update_specs_fills_single_sentinel(tmp_path, capsys):
    src = tmp_path / "t.uncursed"
    src.write_text(
        TEMPLATE
        + "\n#?  PAIR(x, y)\n#=> <???>\n"
        + "\n#?  PAIR(a, b)\n#=>     { a, b }\n"
    )
    assert _run([str(src), "--update-specs"]) == 0
    text = src.read_text()
    assert "<???>" not in text
    assert "#=>     { x, y }" in text
    assert "#=>     { a, b }" in text  # real expectations untouched
    assert "filled 1" in capsys.readouterr().out


def test_update_specs_is_idempotent(tmp_path, capsys):
    src = tmp_path / "t.uncursed"
    src.write_text(TEMPLATE + "\n#?  PAIR(x, y)\n#=> <???>\n")
    assert _run([str(src), "--update-specs"]) == 0
    filled = src.read_text()
    assert _run([str(src), "--update-specs"]) == 0
    assert src.read_text() == filled
    assert "nothing to fill" in capsys.readouterr().out


def test_sentinel_among_other_lines_is_not_filled(tmp_path):
    src = tmp_path / "t.uncursed"
    body = TEMPLATE + "\n#?  PAIR(x, y)\n#=>     { x,\n#=> <???>\n"
    src.write_text(body)
    assert _run([str(src), "--update-specs"]) == 0
    assert "<???>" in src.read_text()  # only single-sentinel specs fill


def test_filled_specs_pass_the_checker(tmp_path):
    from uncursed_pp.speccheck import main as check_main

    src = tmp_path / "t.uncursed"
    src.write_text(TEMPLATE + "\n#?  PAIR(x, y)\n#=> <???>\n")
    assert _run([str(src), "--update-specs"]) == 0
    with pytest.raises(SystemExit) as excinfo:
        check_main([str(src)])
    assert excinfo.value.code == 0


def test_only_one_input_file(tmp_path, capsys):
    a = tmp_path / "a.uncursed"
    a.write_text(TEMPLATE)
    b = tmp_path / "b.uncursed"
    b.write_text(TEMPLATE)
    assert _run([str(a), str(b), "PAIR(1, 2)"]) == 2


@pytest.mark.skipif(shutil.which("clang-format") is None, reason="no clang-format")
def test_format_prettifies_output(tmp_path, capsys):
    src = tmp_path / "t.uncursed"
    src.write_text("@macro FN($n)\nvoid {{$n}}(void) { work(); done(); }\n@endmacro\n")
    assert _run([str(src), "FN(go)", "--format"]) == 0
    out = capsys.readouterr().out
    assert "void go(void)" in out
    assert out.count("\n") >= 2  # clang-format split the statements


def test_format_without_clang_format_is_setup_error(tmp_path, capsys, monkeypatch):
    import uncursed_pp.evaltool as et

    monkeypatch.setattr(et, "_find_clang_format", lambda: None)
    src = tmp_path / "t.uncursed"
    src.write_text(TEMPLATE)
    assert _run([str(src), "PAIR(1, 2)", "--format"]) == 2
    assert "clang-format" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("clang-format") is None, reason="no clang-format")
def test_format_args_reach_clang_format(tmp_path, capsys):
    src = tmp_path / "t.uncursed"
    src.write_text("@macro FN($n)\nvoid {{$n}}(int alpha, int beta, int gamma, int delta);\n@endmacro\n")
    assert _run([str(src), "FN(f)", "--format",
                 "--format-arg=-style={BasedOnStyle: LLVM, ColumnLimit: 20}"]) == 0
    narrow = capsys.readouterr().out
    assert narrow.count("\n") >= 3  # the 20-column limit forces wrapping


@pytest.mark.skipif(shutil.which("clang-format") is None, reason="no clang-format")
def test_bad_format_arg_is_a_clean_error(tmp_path, capsys):
    src = tmp_path / "t.uncursed"
    src.write_text(TEMPLATE)
    assert _run([str(src), "PAIR(1, 2)", "--format", "--format-arg=--definitely-not-a-flag"]) == 2
    assert "clang-format" in capsys.readouterr().err


# ── review-finding regressions ───────────────────────────────────────


def test_update_specs_never_fills_from_a_stale_invocation(tmp_path):
    # a lone sentinel under '#?!' must not be filled using the PREVIOUS
    # '#?' invocation ('#?!' specs cannot have expectations at all)
    src = tmp_path / "t.uncursed"
    src.write_text(TEMPLATE + "\n#?  PAIR(a, b)\n#=>     { a, b }\n\n#?! PAIR(1\n#=> <???>\n")
    assert _run([str(src), "--update-specs"]) == 2
    assert "<???>" in src.read_text()  # nothing rewritten


def test_update_specs_reports_empty_expansions_instead_of_corrupting(tmp_path):
    src = tmp_path / "t.uncursed"
    src.write_text(
        "@macro NOTHING($x)\n@if is_paren($x) @then a @end\n@endmacro\n"
        "\n#?  NOTHING(y)\n#=> <???>\n"
    )
    assert _run([str(src), "--update-specs"]) == 1
    text = src.read_text()
    assert "<???>" in text  # sentinel kept
    assert "\n#=>\n" not in text and not any(
        line.rstrip() == "#=>" for line in text.splitlines()
    )


def test_update_specs_preserves_crlf(tmp_path):
    src = tmp_path / "t.uncursed"
    body = TEMPLATE + "\n#?  PAIR(x, y)\n#=> <???>\n"
    src.write_bytes(body.replace("\n", "\r\n").encode())
    assert _run([str(src), "--update-specs"]) == 0
    raw = src.read_bytes()
    assert b"\r\n" in raw and b"<???>" not in raw
    # no mixed endings: every newline is CRLF
    assert raw.count(b"\n") == raw.count(b"\r\n")
