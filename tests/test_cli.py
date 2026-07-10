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


def test_cli_has_no_config_flags():
    # configuration lives in the .cursed file (@pragma), not on the CLI
    for flag in ["--pp-prefix", "--pp-include", "--pp-include-dir",
                 "--helper-prefix", "--runtime-name", "--include"]:
        with pytest.raises(SystemExit) as excinfo:
            main(["in.cursed", flag, "X"])
        assert excinfo.value.code == 2


def test_cli_config_via_pragmas(tmp_path):
    src = tmp_path / "d.cursed"
    src.write_text(
        "@pragma pp_prefix V_PP_\n"
        '@pragma pp_include "v/pp.hpp"\n'
        "@pragma helper_prefix MY_\n"
        "macro D(xs: seq<token>)\n@for x in xs\nf({{x}});\n@end\nend\n"
    )
    out = tmp_path / "d.h"
    main([str(src), "-o", str(out)])
    text = out.read_text()
    assert "#include <v/pp.hpp>" in text
    assert "V_PP_SEQ_FOR_EACH" in text
    assert "MY_D_EACH1" in text


def test_cli_writes_runtime_header_when_needed(tmp_path):
    src = tmp_path / "w.cursed"
    src.write_text("macro SP(p: tuple<a, b>)\n{{p.a}} {{p.b}}\nend\n")
    out = tmp_path / "sub" / "w.h"
    out.parent.mkdir()
    main([str(src), "-o", str(out)])
    runtime = out.parent / "cursedpp_runtime.h"
    assert runtime.exists()
    assert "CURSEDPP_KW_SPREAD" in runtime.read_text()


def test_cli_no_runtime_for_plain_macros(tmp_path):
    src = tmp_path / "p.cursed"
    src.write_text("macro ID(x)\n{{x}}\nend\n")
    main([str(src)])
    assert not (tmp_path / "cursedpp_runtime.h").exists()


def test_cli_runtime_name_pragma(tmp_path):
    src = tmp_path / "w.cursed"
    src.write_text(
        '@pragma runtime_name "acme_common.h"\n'
        "macro SP(p: tuple<a, b>)\n{{p.a}} {{p.b}}\nend\n"
    )
    main([str(src)])
    assert (tmp_path / "acme_common.h").exists()
    assert '#include "acme_common.h"' in (tmp_path / "w.h").read_text()


def test_cli_extra_include_and_pp_include_dir_pragmas(tmp_path):
    src = tmp_path / "d.cursed"
    src.write_text(
        '@pragma include "myproj/types.h"\n'
        "@pragma include <stdio.h>\n"
        "@pragma pp_include_dir boost_foo/preprocessor\n"
        "macro D(xs: seq<token>)\n@for x in xs\nf({{x}});\n@end\nend\n"
    )
    main([str(src)])
    text = (tmp_path / "d.h").read_text()
    assert '#include "myproj/types.h"' in text
    assert "#include <stdio.h>" in text
    assert "#include <boost_foo/preprocessor/seq/for_each.hpp>" in text


def test_cli_refuses_to_overwrite_input(tmp_path, capsys):
    src = tmp_path / "already.h"
    src.write_text("macro ID(x)\n{{x}}\nend\n")
    with pytest.raises(SystemExit) as excinfo:
        main([str(src)])
    assert excinfo.value.code == 1
    assert "overwrite" in capsys.readouterr().err
    assert src.read_text().startswith("macro ID")  # untouched
