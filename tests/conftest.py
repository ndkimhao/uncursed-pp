"""Shared helpers: the gcc e2e harness and golden-tree discovery.

The heavy lifting (token canonicalization, spec parsing, the capped
preprocessor runner) lives in uncursed_pp.speccheck - one implementation
shared with the `uncursed-pp-check` CLI. This file adds the pytest-side
conveniences: the vendored-boost flags, skip markers, and discovery.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from uncursed_pp import speccheck
from uncursed_pp.emitter import compile_template
from uncursed_pp.speccheck import canon as canon  # re-export for tests

GOLDEN = Path(__file__).parent / "golden"
EXAMPLES = Path(__file__).parent.parent / "examples"

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


def run_cpp(c_file: Path, *extra_flags: str) -> str:
    """Preprocess against the vendored boost; spec failures surface as
    AssertionError so pytest.raises(..., match=...) keeps working."""
    assert CC is not None, "requires_boost should have skipped this test"
    try:
        return speccheck.run_cpp(c_file, cc=CC, flags=[*BOOST_FLAGS, *extra_flags])
    except speccheck.CppError as exc:
        raise AssertionError(str(exc)) from None


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
    """All spec-carrying templates: regression goldens plus the
    human-facing examples tree (a second golden root)."""
    return sorted(GOLDEN.rglob("*.uncursed")) + sorted(EXAMPLES.rglob("*.uncursed"))


def golden_id(path: Path) -> str:
    if path.is_relative_to(EXAMPLES):
        return "examples/" + path.relative_to(EXAMPLES).with_suffix("").as_posix()
    return path.relative_to(GOLDEN).with_suffix("").as_posix()
