import pytest

from cursedpp.cli import main


def test_cli_help_runs(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "cursedpp" in capsys.readouterr().out


def test_cli_compiles_to_default_output(tmp_path, monkeypatch):
    src = tmp_path / "fields.cursed"
    src.write_text("macro ID(x)\n{{x}}\nend\n")
    main([str(src)])
    out = tmp_path / "fields.h"
    assert out.exists()
    assert "#define ID(x) x" in out.read_text()
    assert "#pragma once" in out.read_text()


def test_cli_explicit_output(tmp_path):
    src = tmp_path / "a.cursed"
    src.write_text("macro ID(x)\n{{x}}\nend\n")
    dest = tmp_path / "sub" / "b.h"
    dest.parent.mkdir()
    main([str(src), "-o", str(dest)])
    assert dest.exists()


def test_cli_reports_errors_to_stderr(tmp_path, capsys):
    src = tmp_path / "bad.cursed"
    src.write_text("macro FOO(x)\nno end here\n")
    with pytest.raises(SystemExit) as excinfo:
        main([str(src)])
    assert excinfo.value.code == 1
    assert "bad.cursed" in capsys.readouterr().err
