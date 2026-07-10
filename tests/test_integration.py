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
    return re.sub(r"\s+", " ", result.stdout).strip()


@requires_boost
def test_declare_fields_expansion(tmp_path):
    expanded = preprocess(
        tmp_path, "declare_fields", "DECLARE_FIELDS(((int, x))((float, y))((char *, name)))"
    )
    assert "int x; float y; char * name;" in expanded
