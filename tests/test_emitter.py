from pathlib import Path

import pytest

from cursedpp.emitter import compile_source

GOLDEN = Path(__file__).parent / "golden"


def golden_cases() -> list[str]:
    return sorted(p.stem for p in GOLDEN.glob("*.cursed"))


@pytest.mark.parametrize("name", golden_cases())
def test_golden(name):
    source = (GOLDEN / f"{name}.cursed").read_text()
    expected = (GOLDEN / f"{name}.h").read_text()
    assert compile_source(source, f"{name}.cursed") == expected


def test_plain_macro_single_line():
    out = compile_source("macro ID(x)\n{{x}}\nend\n", "id.cursed")
    assert "#define ID(x) x\n" in out
    assert "#pragma once" in out


def test_multiline_body_uses_continuations():
    src = "macro TWO(a, b)\nfirst {{a}}\nsecond {{b}}\nend\n"
    out = compile_source(src, "two.cursed")
    assert "#define TWO(a, b) \\\n    first a \\\n    second b\n" in out


def test_seq_index_access():
    out = compile_source("macro F2(xs: seq<token>)\n{{xs[0]}}, {{xs[1]}}\nend\n", "f.cursed")
    assert "#define F2(xs) BOOST_PP_SEQ_ELEM(0, xs), BOOST_PP_SEQ_ELEM(1, xs)\n" in out


def test_join_with_non_comma_separator():
    src = 'macro ORS(xs: seq<token>)\n@join xs as x with " || "\n({{x}})\n@end\nend\n'
    out = compile_source(src, "ors.cursed")
    assert "#define CURSEDPP_ORS_SEP1() ||\n" in out
    assert (
        "#define CURSEDPP_ORS_EACH1(r, d, i, e) "
        "BOOST_PP_IF(i, CURSEDPP_ORS_SEP1, BOOST_PP_EMPTY)() (e)\n" in out
    )
    assert "#define ORS(xs) BOOST_PP_SEQ_FOR_EACH_I(CURSEDPP_ORS_EACH1, ~, xs)\n" in out


def test_undefined_variable_is_error():
    from cursedpp.parser import CursedppError

    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(x)\n{{y}}\nend\n", "f.cursed")
    assert "f.cursed:2" in str(excinfo.value)
    assert "y" in str(excinfo.value)


def test_concat_multiple_args_nests_cat():
    out = compile_source("macro F(a)\n{{concat(pre_, a, _post)}}\nend\n", "f.cursed")
    assert "#define F(a) BOOST_PP_CAT(pre_, BOOST_PP_CAT(a, _post))\n" in out


def test_if_branches_share_the_union_of_free_vars():
    src = (
        "macro F(a, b)\n"
        "@if len_unrelated == 0\n"
        "@end\n"
        "end\n"
    )
    # len() must reference a real seq param; unrelated name is an error
    from cursedpp.parser import CursedppError

    with pytest.raises(CursedppError):
        compile_source(src.replace("len_unrelated == 0", "len(nope) == 0"), "f.cursed")


def test_if_comparison_operators_map_to_boost_pp():
    src = "macro F(xs: seq<token>)\n@if len(xs) >= 2\nbig\n@else\nsmall\n@end\nend\n"
    out = compile_source(src, "f.cursed")
    assert "BOOST_PP_IIF(BOOST_PP_GREATER_EQUAL(BOOST_PP_SEQ_SIZE(xs), 2)" in out


def test_empty_else_branch_emits_empty_helper():
    src = "macro F(xs: seq<token>)\n@if len(xs) == 1\nonly\n@end\nend\n"
    out = compile_source(src, "f.cursed")
    # branches reference no variables, so the helpers take zero parameters
    assert "#define CURSEDPP_F_ELSE1()\n" in out
    assert "CURSEDPP_F_THEN1, CURSEDPP_F_ELSE1)()" in out


LOG_SRC = 'macro LOG(msg, level = INFO, out = stderr)\nfprintf({{out}}, "[" #{{level}} "] %s\\n", {{msg}});\nend\n'


def test_tail_defaults_emit_overload_chain():
    out = compile_source(LOG_SRC, "log.cursed")
    assert "#define CURSEDPP_LOG_1(msg) CURSEDPP_LOG_3(msg, INFO, stderr)\n" in out
    assert "#define CURSEDPP_LOG_2(msg, level) CURSEDPP_LOG_3(msg, level, stderr)\n" in out
    assert "#define CURSEDPP_LOG_3(msg, level, out)" in out
    assert "#define LOG(...) BOOST_PP_OVERLOAD(CURSEDPP_LOG_, __VA_ARGS__)(__VA_ARGS__)\n" in out


def test_default_after_required_only():
    from cursedpp.parser import CursedppError

    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(a = 1, b)\nx\nend\n", "f.cursed")
    assert "default" in str(excinfo.value)


def test_mixing_defaults_and_named_is_error():
    from cursedpp.parser import CursedppError

    with pytest.raises(CursedppError):
        compile_source("macro F(a, b = 1, named C = 2)\nx\nend\n", "f.cursed")


WIDGET_SRC = (
    "macro MAKE_WIDGET(name, named WIDTH = 100, named HEIGHT = 50, named FLAGS = )\n"
    "struct widget {{name}} = { {{WIDTH}}, {{HEIGHT}}, {{FLAGS}} };\n"
    "end\n"
)


def test_named_args_emit_probe_and_fold():
    out = compile_source(WIDGET_SRC, "widget.cursed")
    assert "#define CURSEDPP_MAKE_WIDGET_KW_WIDTH_WIDTH(v) v, 1\n" in out
    assert "BOOST_PP_SEQ_FOLD_LEFT" in out
    assert "#define CURSEDPP_MAKE_WIDGET_1(name) CURSEDPP_MAKE_WIDGET_BODY(name, 100, 50, )\n" in out
    assert (
        "#define MAKE_WIDGET(...) "
        "BOOST_PP_OVERLOAD(CURSEDPP_MAKE_WIDGET_, __VA_ARGS__)(__VA_ARGS__)\n" in out
    )


FOO_VARIADIC_SRC = (
    "macro FOO(items: variadic)\n"
    'S{ @join items as it with ", ": @if is_paren(it) {{it}} @else ({{it}}, omit) @end@end }\n'
    "end\n"
)


def test_variadic_param_compiles_to_va_args_seq():
    out = compile_source(FOO_VARIADIC_SRC, "foo.cursed")
    assert "#define FOO(...)" in out
    assert "BOOST_PP_VARIADIC_TO_SEQ(__VA_ARGS__)" in out


def test_variadic_must_be_last():
    from cursedpp.parser import CursedppError

    with pytest.raises(CursedppError):
        compile_source("macro F(items: variadic, x)\n{{x}}\nend\n", "f.cursed")


def test_variadic_excludes_defaults():
    from cursedpp.parser import CursedppError

    with pytest.raises(CursedppError):
        compile_source("macro F(a = 1, items: variadic)\n{{a}}\nend\n", "f.cursed")


def test_pragma_pp_prefix_and_include():
    src = (
        "@pragma pp_prefix MYLIB_PP_\n"
        '@pragma pp_include "mylib/preprocessor.hpp"\n'
        "macro DECL(fields: seq<tuple<type, name>>)\n"
        "@for (type, name) in fields\n"
        "  {{type}} {{name}};\n"
        "@end\n"
        "end\n"
    )
    out = compile_source(src, "t.cursed")
    assert '#include <mylib/preprocessor.hpp>' in out
    assert "MYLIB_PP_SEQ_FOR_EACH" in out
    assert "MYLIB_PP_TUPLE_ELEM" in out
    assert "BOOST_PP_" not in out


def test_pragma_helper_prefix():
    src = "@pragma helper_prefix VENDORED_\nmacro D(xs: seq<token>)\n@for x in xs\nf({{x}});\n@end\nend\n"
    out = compile_source(src, "t.cursed")
    assert "#define VENDORED_D_EACH1(r, d, e) f(e);" in out
    assert "CURSEDPP_" not in out


def test_custom_pp_prefix_requires_include():
    from cursedpp.parser import CursedppError

    with pytest.raises(CursedppError) as excinfo:
        compile_source("@pragma pp_prefix MYPP_\nmacro ID(x)\n{{x}}\nend\n", "t.cursed")
    assert "pp_include" in str(excinfo.value)
