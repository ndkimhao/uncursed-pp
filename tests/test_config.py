"""Configuration: pragmas, prefixes, includes, and the runtime header."""

from pathlib import Path

import pytest

from conftest import GOLDEN
from uncursed_pp.emitter import EmitConfig, compile_source, compile_template, runtime_header
from uncursed_pp.parser import UncursedPpError, parse_file

# a macro that needs the shared runtime (spread tuple param -> KW_SPREAD)
SPREAD_SRC = "@macro SP(p: tuple<a, b>)\n{{p.a}} {{p.b}}\n@endmacro\n"


def test_parse_pragmas():
    src = (
        "@pragma pp_prefix MYLIB_PP_\n"
        '@pragma pp_include "mylib/preprocessor.hpp"\n'
        "@macro ID(x)\n{{x}}\n@endmacro\n"
    )
    file = parse_file(src, "t.uncursed")
    assert file.pragmas == {
        "pp_prefix": "MYLIB_PP_",
        "pp_include": "mylib/preprocessor.hpp",
    }


def test_unknown_pragma_is_error():
    with pytest.raises(UncursedPpError) as excinfo:
        parse_file("@pragma nonsense abc\n@macro ID(x)\n{{x}}\n@endmacro\n", "t.uncursed")
    assert "t.uncursed:1" in str(excinfo.value)


def test_pragma_pp_prefix_and_include():
    src = (
        "@pragma pp_prefix MYLIB_PP_\n"
        '@pragma pp_include "mylib/preprocessor.hpp"\n'
        "@macro DECL(fields: seq<tuple<type, name>>)\n"
        "@for (type, name) in fields\n"
        "  {{type}} {{name}};\n"
        "@end\n"
        "@endmacro\n"
    )
    out = compile_source(src, "t.uncursed")
    assert "#include <mylib/preprocessor.hpp>" in out
    assert "MYLIB_PP_SEQ_FOR_EACH" in out
    assert "MYLIB_PP_" in out and "BOOST_PP_" not in out
    assert "BOOST_PP_" not in out


def test_pragma_helper_prefix():
    src = "@pragma helper_prefix VENDORED_\n@macro D(xs: seq<token>)\n@for x in xs\nf({{x}});\n@end\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "#define VENDORED_D_EACH1(r, d, e) f(e);" in out
    assert "UNCURSED_PP_" not in out


def test_custom_pp_prefix_requires_include():
    with pytest.raises(UncursedPpError) as excinfo:
        compile_source("@pragma pp_prefix MYPP_\n@macro ID(x)\n{{x}}\n@endmacro\n", "t.uncursed")
    assert "pp_include" in str(excinfo.value)


def test_custom_pp_prefix_ok_with_custom_include_dir():
    src = "@pragma pp_prefix MYPP_\n@pragma pp_include_dir vendored/pp\n@macro ID(x)\n{{x}}\n@endmacro\n"
    out = compile_source(src, "t.uncursed")
    assert "MYPP_" not in out  # plain macro uses no primitives; compiles fine


def test_extra_includes_via_config():
    out = compile_source(
        "@macro ID(x)\n{{x}}\n@endmacro\n",
        "t.uncursed",
        config=EmitConfig(extra_includes=("myproj/types.h", "<stdio.h>")),
    )
    assert '#include "myproj/types.h"\n' in out
    assert "#include <stdio.h>\n" in out


def test_extra_includes_via_pragma_repeatable_ordered():
    src = (
        '@pragma include "first.h"\n'
        "@pragma include <second.h>\n"
        "@macro ID(x)\n{{x}}\n@endmacro\n"
    )
    out = compile_source(src, "t.uncursed")
    assert out.index('#include "first.h"') < out.index("#include <second.h>")


def test_pp_include_dir_rewrites_granular_includes():
    src = "@macro D(xs: seq<token>)\n@for x in xs\nf({{x}});\n@end\n@endmacro\n"
    out = compile_source(src, "t.uncursed", config=EmitConfig(pp_include_dir="boost_foo/preprocessor"))
    assert "#include <boost_foo/preprocessor/seq/for_each.hpp>" in out
    assert "boost/preprocessor/" not in out


def test_runtime_is_self_contained():
    # the runtime needs no boost primitives at all since slot replacement
    # moved to generated per-slot macros
    rt = runtime_header(EmitConfig(pp_include_dir="boost_foo/preprocessor"))
    assert "#include" not in rt


WIDGET_SRC = (
    "@macro W(name, named WIDTH = 100)\n"
    "struct widget {{name}} = { {{WIDTH}} };\n"
    "@endmacro\n"
)


def test_runtime_header_matches_golden():
    golden = GOLDEN / "uncursed_pp_runtime.h"
    assert runtime_header(EmitConfig()) == golden.read_text()


def test_runtime_header_contents():
    rt = runtime_header(EmitConfig())
    assert "#pragma once" in rt
    assert "#define UNCURSED_PP_KW_SPREAD(...) __VA_ARGS__" in rt
    assert "TUPLE_REPLACE" not in rt
    assert "shared by all uncursed-pp-generated headers" in rt


def test_compile_template_reports_runtime_dependency():
    result = compile_template(SPREAD_SRC, "w.uncursed")
    assert result.runtime is not None
    assert result.runtime_name == "uncursed_pp_runtime.h"

    plain = compile_template("@macro ID(x)\n{{x}}\n@endmacro\n", "id.uncursed")
    assert plain.runtime is None


def test_runtime_name_customizable():
    result = compile_template(
        SPREAD_SRC, "w.uncursed", config=EmitConfig(runtime_name="acme_common.h")
    )
    assert result.runtime_name == "acme_common.h"
    assert '#include "acme_common.h"' in result.header


def test_runtime_name_pragma():
    src = '@pragma runtime_name "acme_common.h"\n' + SPREAD_SRC
    result = compile_template(src, "w.uncursed")
    assert result.runtime_name == "acme_common.h"
    assert '#include "acme_common.h"' in result.header


def test_pp_include_dir_e2e_through_gcc(tmp_path):
    """A rebased include root actually resolves and expands: symlink
    tmp/acme_pp -> the real boost/preprocessor and preprocess."""
    from conftest import BOOST_FLAGS, CC, canon, run_cpp

    if not (CC and BOOST_FLAGS):
        pytest.skip("needs cc and the vendored boost")
    real = Path(BOOST_FLAGS[1]) / "boost" / "preprocessor"
    (tmp_path / "acme_pp").symlink_to(real, target_is_directory=True)

    src = (
        "@pragma pp_include_dir acme_pp\n"
        "@macro D(xs: seq<token>)\n@for x in xs\nf({{x}});\n@end\n@endmacro\n"
    )
    result = compile_template(src, "d.uncursed")
    assert "#include <acme_pp/seq/for_each.hpp>" in result.header
    (tmp_path / "d.h").write_text(result.header)
    if result.runtime is not None:  # chains pull the shared runtime
        (tmp_path / result.runtime_name).write_text(result.runtime)
    (tmp_path / "main.c").write_text('#include "d.h"\nD((a)(b))\n')
    # the boost root stays on the include path: the rebased headers still
    # resolve their own nested <boost/preprocessor/...> includes through it
    out = run_cpp(tmp_path / "main.c", "-I", str(tmp_path))
    assert canon("f(a); f(b);") in canon(out)


def test_committed_example_runtime_headers_are_fresh():
    """Companion runtime headers committed under examples/ must match
    what the current emitter generates for their prefix — generated
    files never depend on a human remembering to regenerate them."""
    prefixes = {"uncursed_pp_runtime.h": "UNCURSED_PP_", "acme_runtime.h": "ACME_"}
    examples = Path(__file__).parent.parent / "examples"
    found = sorted(examples.rglob("*runtime.h"))
    assert found, "expected committed runtime companions under examples/"
    for p in found:
        expected = runtime_header(EmitConfig(helper_prefix=prefixes[p.name]))
        assert p.read_text() == expected, f"stale companion: {p}"


# ── runtime_include: fully custom #include line for the companion ────


def test_runtime_include_pragma_quoted_path():
    src = '@pragma runtime_include "myproj/pp/rt.h"\n' + SPREAD_SRC
    result = compile_template(src, "w.uncursed")
    assert '#include "myproj/pp/rt.h"' in result.header
    assert '"uncursed_pp_runtime.h"' not in result.header
    # needs-runtime detection must follow the custom line
    assert result.runtime is not None
    # the WRITTEN filename is still runtime_name's business
    assert result.runtime_name == "uncursed_pp_runtime.h"


def test_runtime_include_pragma_angle_form():
    src = "@pragma runtime_include <acme/pp_runtime.h>\n" + SPREAD_SRC
    result = compile_template(src, "w.uncursed")
    assert "#include <acme/pp_runtime.h>" in result.header
    assert result.runtime is not None


def test_runtime_include_config_api():
    result = compile_template(
        SPREAD_SRC, "w.uncursed", config=EmitConfig(runtime_include="pp/rt.h")
    )
    assert '#include "pp/rt.h"' in result.header


def test_default_runtime_include_is_quoted_runtime_name():
    result = compile_template(SPREAD_SRC, "w.uncursed")
    assert '#include "uncursed_pp_runtime.h"' in result.header


from conftest import requires_boost, run_cpp, canon  # noqa: E402


@requires_boost
def test_runtime_include_path_resolves_e2e(tmp_path):
    src = '@pragma runtime_include "myproj/pp/rt.h"\n' + SPREAD_SRC
    result = compile_template(src, "w.uncursed")
    (tmp_path / "w.h").write_text(result.header)
    rt = tmp_path / "myproj" / "pp" / "rt.h"
    rt.parent.mkdir(parents=True)
    assert result.runtime is not None
    rt.write_text(result.runtime)
    snippet = tmp_path / "main.c"
    snippet.write_text('#include "w.h"\nSP((int, x))\n')
    assert canon(run_cpp(snippet)) == canon("int x")


def test_readme_documents_every_pragma():
    """The README's pragma table must cover everything the parser accepts."""
    from uncursed_pp.parser import _KNOWN_PRAGMAS

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    table = readme.split("| Pragma |", 1)[1].split("```", 1)[0]
    missing = [p for p in sorted(_KNOWN_PRAGMAS) if f"`{p}`" not in table]
    assert not missing, f"pragmas absent from README table: {missing}"
