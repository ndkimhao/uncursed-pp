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
    assert "#ifndef ID_CURSED_H" in out


def test_multiline_body_uses_continuations():
    src = "macro TWO(a, b)\nfirst {{a}}\nsecond {{b}}\nend\n"
    out = compile_source(src, "two.cursed")
    assert "#define TWO(a, b) \\\n    first a \\\n    second b\n" in out
