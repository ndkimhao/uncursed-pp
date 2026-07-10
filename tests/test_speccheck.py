"""The standalone `uncursed-pp-check` spec checker. The templates here
use no Boost.PP primitives, so a bare C compiler is enough."""

import pytest

from conftest import CC
from uncursed_pp.speccheck import main

pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler available")

PASSING = '@macro ID($x)\n{{$x}}\n@endmacro\n#?  ID(7)\n#=>     7\n'


def _run(argv):
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    return excinfo.value.code


def test_passing_file_exits_zero(tmp_path, capsys):
    src = tmp_path / "ok.uncursed"
    src.write_text(PASSING)
    assert _run([str(src)]) == 0
    out = capsys.readouterr().out
    assert "ok    ID(7)" in out
    assert "1/1 specs passed" in out


def test_failing_expectation_exits_one_with_diff(tmp_path, capsys):
    src = tmp_path / "bad.uncursed"
    src.write_text('@macro ID($x)\n{{$x}}\n@endmacro\n#?  ID(7)\n#=>     8\n')
    assert _run([str(src)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  ID(7)" in out
    assert "expected: 8" in out and "actual:   7" in out


def test_expected_failure_spec_passes(tmp_path):
    src = tmp_path / "xfail.uncursed"
    # unbalanced parens at the call site: preprocessing must fail
    src.write_text('@macro ID($x)\n{{$x}}\n@endmacro\n#?! ID(\n')
    assert _run([str(src)]) == 0


def test_expected_failure_that_succeeds_exits_one(tmp_path, capsys):
    src = tmp_path / "notfail.uncursed"
    src.write_text('@macro ID($x)\n{{$x}}\n@endmacro\n#?! ID(7)\n')
    assert _run([str(src)]) == 1
    assert "expected preprocessing to FAIL" in capsys.readouterr().out


def test_no_specs_is_a_setup_error(tmp_path, capsys):
    src = tmp_path / "nospecs.uncursed"
    src.write_text('@macro ID($x)\n{{$x}}\n@endmacro\n')
    assert _run([str(src)]) == 2
    assert "no #? specs" in capsys.readouterr().err


def test_template_error_is_a_setup_error(tmp_path, capsys):
    src = tmp_path / "broken.uncursed"
    src.write_text("@macro ID($x)\n{{$x}}\n#?  ID(1)\n#=> 1\n")  # missing @endmacro
    assert _run([str(src)]) == 2
    assert "@endmacro" in capsys.readouterr().err


def test_missing_compiler_is_a_setup_error(tmp_path, capsys):
    src = tmp_path / "ok.uncursed"
    src.write_text(PASSING)
    assert _run([str(src), "--cc", "definitely-not-a-compiler"]) == 2
    assert "compiler not found" in capsys.readouterr().err


def test_missing_input_is_a_setup_error(tmp_path, capsys):
    assert _run([str(tmp_path / "nope.uncursed")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_flags_after_dashes_reach_the_compiler(tmp_path):
    src = tmp_path / "flag.uncursed"
    # VAL is free C text; only -DVAL=2 makes the expectation hold
    src.write_text("@macro V()\nVAL\n@endmacro\n#?  V()\n#=>     2\n")
    assert _run([str(src)]) == 1
    assert _run([str(src), "--", "-DVAL=2"]) == 0


def test_multiple_inputs_one_failing_exits_one(tmp_path, capsys):
    good = tmp_path / "good.uncursed"
    good.write_text(PASSING)
    bad = tmp_path / "bad.uncursed"
    bad.write_text('@macro ID($x)\n{{$x}}\n@endmacro\n#?  ID(7)\n#=>     8\n')
    assert _run([str(good), str(bad)]) == 1
    out = capsys.readouterr().out
    assert "good.uncursed: 1/1 specs passed" in out
    assert "bad.uncursed: 0/1 specs passed" in out


def test_work_dir_keeps_artifacts(tmp_path):
    src = tmp_path / "dbg.uncursed"
    src.write_text(PASSING)
    work = tmp_path / "scratch"
    assert _run([str(src), "--work-dir", str(work)]) == 0
    # artifacts live in a per-template subdirectory (same-stem templates
    # from different directories must not clobber each other)
    [header] = work.rglob("dbg.h")
    [snippet] = work.rglob("dbg_spec_1.c")
    assert '#include "dbg.h"' in snippet.read_text()


def test_directory_input_checks_all_templates_recursively(tmp_path, capsys):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.uncursed").write_text(PASSING)
    (tmp_path / "sub" / "b.uncursed").write_text(
        '@macro ID($x)\n{{$x}}\n@endmacro\n#?  ID(7)\n#=>     8\n'
    )
    assert _run([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "a.uncursed: 1/1 specs passed" in out
    assert "b.uncursed: 0/1 specs passed" in out


def test_directory_without_templates_is_a_setup_error(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert _run([str(tmp_path / "empty")]) == 2
    assert "no .uncursed templates" in capsys.readouterr().err


def test_mixed_file_and_directory_inputs(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "a.uncursed").write_text(PASSING)
    lone = tmp_path / "lone.uncursed"
    lone.write_text(PASSING)
    assert _run([str(lone), str(d)]) == 0


def test_multi_file_output_has_separators_and_summary(tmp_path, capsys):
    good = tmp_path / "good.uncursed"
    good.write_text(PASSING)
    bad = tmp_path / "bad.uncursed"
    bad.write_text('@macro ID($x)\n{{$x}}\n@endmacro\n#?  ID(7)\n#=>     8\n')
    also = tmp_path / "also.uncursed"
    also.write_text(PASSING)
    assert _run([str(good), str(bad), str(also)]) == 1
    out = capsys.readouterr().out
    # a blank line separates file sections
    assert "specs passed\n\nok" in out or "specs passed\n\nFAIL" in out
    # grand summary with totals and the failing files called out
    assert "3 files: 2/3 specs passed" in out
    assert "FAILED bad.uncursed" in out or f"FAILED {bad}" in out


def test_single_file_has_no_grand_summary(tmp_path, capsys):
    src = tmp_path / "one.uncursed"
    src.write_text(PASSING)
    assert _run([str(src)]) == 0
    assert "files:" not in capsys.readouterr().out


# ── color: only on an interactive terminal, NO_COLOR wins ────────────


def test_no_ansi_when_not_a_tty(tmp_path, capsys):
    src = tmp_path / "plain.uncursed"
    src.write_text(PASSING)
    _run([str(src)])
    assert "\x1b[" not in capsys.readouterr().out


def test_ansi_when_terminal_detected(tmp_path, capsys, monkeypatch):
    import uncursed_pp.speccheck as sc

    monkeypatch.setattr(sc, "_use_color", lambda: True)
    src = tmp_path / "color.uncursed"
    src.write_text(PASSING)
    _run([str(src)])
    out = capsys.readouterr().out
    assert "\x1b[32m" in out  # green ok tag


def test_use_color_detection(monkeypatch):
    import sys
    from types import SimpleNamespace

    import uncursed_pp.speccheck as sc

    monkeypatch.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: True))
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert sc._use_color() is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert sc._use_color() is False
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: False))
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert sc._use_color() is False


# ── review-finding regressions ───────────────────────────────────────


def test_canon_keeps_pp_numbers_whole():
    from uncursed_pp.speccheck import canon

    # '1e+5' is ONE C pp-number; splitting at the exponent sign made
    # float specs compare equal to genuinely different token streams
    assert canon("1e+5") != canon("1e + 5")
    assert canon("1.5e-3f") == "1.5e-3f"
    assert canon("x+5") == "x + 5"  # identifier + operator still split


def test_expect_failure_does_not_pass_on_timeout(tmp_path):
    from uncursed_pp.speccheck import SpecResult, check_file

    src = tmp_path / "t.uncursed"
    src.write_text('@macro ID($x)\n{{$x}}\n@endmacro\n#?! ID(7)\n')
    # a timeout is not evidence the invocation is invalid: with an
    # absurdly small timeout the #?! spec must FAIL, not pass
    [result] = check_file(src, cc="cc", timeout=1e-9)
    assert not result.ok
    assert "timed out" in result.detail


def test_work_dir_separates_same_stem_templates(tmp_path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir(), b_dir.mkdir()
    (a_dir / "x.uncursed").write_text(
        '@macro FA($v)\nfa({{$v}})\n@endmacro\n#?  FA(1)\n#=>     fa(1)\n'
    )
    (b_dir / "x.uncursed").write_text(
        '@macro FB($v)\nfb({{$v}})\n@endmacro\n#?  FB(1)\n#=>     fb(1)\n'
    )
    work = tmp_path / "scratch"
    assert _run([str(a_dir / "x.uncursed"), str(b_dir / "x.uncursed"),
                 "--work-dir", str(work)]) == 0
    headers = sorted(p for p in work.rglob("x.h"))
    assert len(headers) == 2, headers
    texts = [h.read_text() for h in headers]
    assert any("FA" in t for t in texts) and any("FB" in t for t in texts)


def test_directory_discovery_follows_symlinked_subdirs(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "t.uncursed").write_text(PASSING)
    root = tmp_path / "root"
    root.mkdir()
    (root / "linked").symlink_to(real, target_is_directory=True)
    assert _run([str(root)]) == 0
