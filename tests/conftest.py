"""Shared helpers: the gcc e2e harness and golden-tree discovery."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cursedpp.emitter import compile_template

GOLDEN = Path(__file__).parent / "golden"

CC = shutil.which("cc") or shutil.which("gcc")

# Vendored Boost.Preprocessor (make boost-pp / make setup clones it);
# falls back to a system boost when the vendored copy is absent.
_VENDORED_BOOST = Path(__file__).parent.parent / ".boost-pp" / "include"
BOOST_FLAGS = ["-I", str(_VENDORED_BOOST)] if _VENDORED_BOOST.is_dir() else []


def _boost_available() -> bool:
    if CC is None:
        return False
    probe = subprocess.run(
        [CC, "-E", "-P", "-x", "c", *BOOST_FLAGS, "-"],
        input="#include <boost/preprocessor.hpp>\n",
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


requires_boost = pytest.mark.skipif(
    not _boost_available(), reason="boost/preprocessor.hpp not available"
)


def canon(text: str) -> str:
    """Whitespace-canonical form: the preprocessor may add/drop spaces around
    punctuation, but never inside identifiers - so compare modulo that."""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r" ?([(),;{}|]) ?", r"\1", text)


def preprocess_src(tmp_path: Path, source: str, stem: str, invocation: str) -> str:
    """Compile a template, include it from a snippet, run cc -E -P."""
    header = tmp_path / f"{stem}.h"
    result = compile_template(source, f"{stem}.cursed")
    header.write_text(result.header)
    if result.runtime is not None:
        (tmp_path / result.runtime_name).write_text(result.runtime)
    snippet = tmp_path / "main.c"
    snippet.write_text(f'#include "{header.name}"\n{invocation}\n')
    run = subprocess.run(
        [CC, "-E", "-P", *BOOST_FLAGS, str(snippet)],
        capture_output=True,
        text=True,
        check=True,
    )
    return canon(run.stdout)


def golden_templates() -> list[Path]:
    """All golden templates, recursively (goldens are organized by category)."""
    return sorted(GOLDEN.rglob("*.cursed"))


def golden_id(path: Path) -> str:
    return path.relative_to(GOLDEN).with_suffix("").as_posix()
