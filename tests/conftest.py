"""Shared helpers: the gcc e2e harness and golden-tree discovery."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from uncursed_pp.emitter import C_LITERAL_PATTERN, compile_template

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


# Locally, missing cc/boost skips the e2e layer; in CI (GitHub sets CI=1)
# that would silently hollow out the suite - fail loudly instead.
if os.environ.get("CI") and not _boost_available():
    raise RuntimeError(
        "CI requires the gcc e2e layer: cc/gcc and Boost.PP must be present "
        "(did 'make boost-pp' run?)"
    )

requires_boost = pytest.mark.skipif(
    not _boost_available(), reason="boost/preprocessor.hpp not available"
)


_C_TOKEN = re.compile(
    C_LITERAL_PATTERN      # raw/prefixed string and char literals, verbatim
    + r"|[A-Za-z_]\w*"     # identifier
    + r"|\d[\w.]*"        # number
    + r"|\S",              # any punctuation char
    re.S,
)


def canon(text: str) -> str:
    """Token-exact canonical form: whitespace BETWEEN C tokens is
    insignificant and normalized away, but string/char literal interiors
    (including C++ raw strings) are preserved verbatim - two expansions
    compare equal iff their token streams are identical."""
    return " ".join(m.group(0) for m in _C_TOKEN.finditer(text))


def run_cpp(c_file: Path, *extra_flags: str) -> str:
    """Preprocess a C file; failures surface the compiler's stderr instead
    of an opaque CalledProcessError."""
    assert CC is not None, "requires_boost should have skipped this test"
    cmd = [CC, "-E", "-P", *BOOST_FLAGS, *extra_flags, str(c_file)]
    run = subprocess.run(cmd, capture_output=True, text=True)
    if run.returncode != 0:
        raise AssertionError(
            f"preprocessing failed: {' '.join(cmd)}\n--- compiler stderr ---\n{run.stderr}"
        )
    return run.stdout


def preprocess_src(tmp_path: Path, source: str, stem: str, invocation: str) -> str:
    """Compile a template, include it from a snippet, run cc -E -P."""
    header = tmp_path / f"{stem}.h"
    result = compile_template(source, f"{stem}.uncursed")
    header.write_text(result.header)
    if result.runtime is not None:
        (tmp_path / result.runtime_name).write_text(result.runtime)
    snippet = tmp_path / "main.c"
    snippet.write_text(f'#include "{header.name}"\n{invocation}\n')
    return canon(run_cpp(snippet))


def golden_templates() -> list[Path]:
    """All golden templates, recursively (goldens are organized by category)."""
    return sorted(GOLDEN.rglob("*.uncursed"))


def golden_id(path: Path) -> str:
    return path.relative_to(GOLDEN).with_suffix("").as_posix()
