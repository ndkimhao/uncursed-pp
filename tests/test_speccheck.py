"""The standalone `uncursed-pp-check` spec checker. The templates here
use no Boost.PP primitives, so a bare C compiler is enough."""

import pytest

from conftest import CC
from uncursed_pp.speccheck import main

pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler available")

PASSING = "@macro ID(x)\n{{x}}\n@endmacro\n#?  ID(7)\n#=>     7\n"


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
    src.write_text("@macro ID(x)\n{{x}}\n@endmacro\n#?  ID(7)\n#=>     8\n")
    assert _run([str(src)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  ID(7)" in out
    assert "expected: 8" in out and "actual:   7" in out


def test_expected_failure_spec_passes(tmp_path):
    src = tmp_path / "xfail.uncursed"
    # unbalanced parens at the call site: preprocessing must fail
    src.write_text("@macro ID(x)\n{{x}}\n@endmacro\n#?! ID(\n")
    assert _run([str(src)]) == 0


def test_expected_failure_that_succeeds_exits_one(tmp_path, capsys):
    src = tmp_path / "notfail.uncursed"
    src.write_text("@macro ID(x)\n{{x}}\n@endmacro\n#?! ID(7)\n")
    assert _run([str(src)]) == 1
    assert "expected preprocessing to FAIL" in capsys.readouterr().out


def test_no_specs_is_a_setup_error(tmp_path, capsys):
    src = tmp_path / "nospecs.uncursed"
    src.write_text("@macro ID(x)\n{{x}}\n@endmacro\n")
    assert _run([str(src)]) == 2
    assert "no #? specs" in capsys.readouterr().err


def test_template_error_is_a_setup_error(tmp_path, capsys):
    src = tmp_path / "broken.uncursed"
    src.write_text("@macro ID(x)\n{{x}}\n#?  ID(1)\n#=> 1\n")  # missing @endmacro
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
    bad.write_text("@macro ID(x)\n{{x}}\n@endmacro\n#?  ID(7)\n#=>     8\n")
    assert _run([str(good), str(bad)]) == 1
    out = capsys.readouterr().out
    assert "good.uncursed: 1/1 specs passed" in out
    assert "bad.uncursed: 0/1 specs passed" in out


def test_work_dir_keeps_artifacts(tmp_path):
    src = tmp_path / "dbg.uncursed"
    src.write_text(PASSING)
    work = tmp_path / "scratch"
    assert _run([str(src), "--work-dir", str(work)]) == 0
    assert (work / "dbg.h").exists()
    assert (work / "dbg_spec_1.c").exists()
    assert '#include "dbg.h"' in (work / "dbg_spec_1.c").read_text()


def test_directory_input_checks_all_templates_recursively(tmp_path, capsys):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.uncursed").write_text(PASSING)
    (tmp_path / "sub" / "b.uncursed").write_text(
        "@macro ID(x)\n{{x}}\n@endmacro\n#?  ID(7)\n#=>     8\n"
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
