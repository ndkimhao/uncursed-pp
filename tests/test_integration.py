"""End-to-end tests: generated headers run through the real C preprocessor."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cursedpp.emitter import compile_source

GOLDEN = Path(__file__).parent / "golden"

CC = shutil.which("cc") or shutil.which("gcc")


def _boost_available() -> bool:
    if CC is None:
        return False
    probe = subprocess.run(
        [CC, "-E", "-P", "-x", "c", "-"],
        input="#include <boost/preprocessor.hpp>\n",
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


requires_boost = pytest.mark.skipif(
    not _boost_available(), reason="boost/preprocessor.hpp not available"
)


def preprocess(tmp_path: Path, cursed_name: str, invocation: str) -> str:
    """Compile a golden template, include it from a snippet, run cc -E -P."""
    source = (GOLDEN / f"{cursed_name}.cursed").read_text()
    header = tmp_path / f"{cursed_name}.h"
    header.write_text(compile_source(source, f"{cursed_name}.cursed"))
    snippet = tmp_path / "main.c"
    snippet.write_text(f'#include "{header.name}"\n{invocation}\n')
    result = subprocess.run(
        [CC, "-E", "-P", str(snippet)],
        capture_output=True,
        text=True,
        check=True,
    )
    return canon(result.stdout)


def canon(text: str) -> str:
    """Whitespace-canonical form: the preprocessor may add/drop spaces around
    punctuation, but never inside identifiers - so compare modulo that."""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r" ?([(),;{}|]) ?", r"\1", text)


@requires_boost
def test_declare_fields_expansion(tmp_path):
    expanded = preprocess(
        tmp_path, "declare_fields", "DECLARE_FIELDS(((int, x))((float, y))((char *, name)))"
    )
    assert canon("int x; float y; char * name;") in expanded


@requires_boost
def test_proto_expansion(tmp_path):
    expanded = preprocess(
        tmp_path, "proto", "PROTO(draw, ((struct ctx *, ctx))((int, flags)))"
    )
    assert canon("void draw(struct ctx * ctx, int flags);") in expanded


@requires_boost
def test_getter_concat_expansion(tmp_path):
    expanded = preprocess(tmp_path, "getter", "GETTER((int, age))")
    assert canon("int get_age(const struct self *s) { return s->age; }") in expanded


@requires_boost
def test_pair_remove_parens_expansion(tmp_path):
    expanded = preprocess(tmp_path, "pair", "PAIR(((pair<int,int>), b))")
    assert canon("S{ pair<int,int> | b }") in expanded


@requires_boost
def test_pair_without_parens_passthrough(tmp_path):
    expanded = preprocess(tmp_path, "pair", "PAIR((a, b))")
    assert canon("S{ a | b }") in expanded
