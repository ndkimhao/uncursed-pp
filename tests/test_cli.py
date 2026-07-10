import pytest

from cursedpp.cli import main


def test_cli_help_runs(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "cursedpp" in capsys.readouterr().out
