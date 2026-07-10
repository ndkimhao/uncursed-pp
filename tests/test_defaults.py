"""Tail defaults (positional, arity-dispatched).

Codegen shape and runtime behavior live in tests/golden/args/log.*.
"""

import pytest

from cursedpp.emitter import compile_source
from cursedpp.parser import CursedppError, parse_file


def test_parse_tail_defaults():
    src = "macro LOG(msg, level = INFO, out = stderr)\nf({{msg}}, {{level}}, {{out}});\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    assert [(p.name, p.default, p.named) for p in macro.params] == [
        ("msg", None, False),
        ("level", "INFO", False),
        ("out", "stderr", False),
    ]


def test_parse_empty_default():
    src = "macro ATTR(name, qualifiers = )\n{{qualifiers}} int {{name}};\nend\n"
    [macro] = parse_file(src, "t.cursed").macros
    assert macro.params[1].default == ""


def test_overload_chain_covers_every_arity():
    src = "macro LOG(msg, level = INFO, out = stderr)\nf({{msg}});\nend\n"
    out = compile_source(src, "t.cursed")
    assert "#define CURSEDPP_LOG_1(msg) CURSEDPP_LOG_3(msg, INFO, stderr)\n" in out
    assert "#define CURSEDPP_LOG_2(msg, level) CURSEDPP_LOG_3(msg, level, stderr)\n" in out
    assert "#define LOG(...) BOOST_PP_OVERLOAD(CURSEDPP_LOG_, __VA_ARGS__)(__VA_ARGS__)\n" in out


def test_required_after_defaulted_is_error():
    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(a = 1, b)\nx\nend\n", "f.cursed")
    assert "default" in str(excinfo.value)


def test_mixing_defaults_and_named_is_error():
    with pytest.raises(CursedppError):
        compile_source("macro F(a, b = 1, named C = 2)\nx\nend\n", "f.cursed")


def test_all_params_defaulted_is_error():
    with pytest.raises(CursedppError) as excinfo:
        compile_source("macro F(a = 1)\n{{a}}\nend\n", "f.cursed")
    assert "required parameter" in str(excinfo.value)
