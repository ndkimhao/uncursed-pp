"""Verify a template's inline `#?` / `#=>` specs through a real C
preprocessor.

This module is both the `uncursed-pp-check` CLI and the single
implementation of the token canonicalizer, spec parser, and capped
preprocessor runner that the test harness uses — the tool and the test
suite cannot drift apart.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .emitter import C_LITERAL_PATTERN, CompileResult, compile_template
from .parser import UncursedPpError

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


Spec = tuple[str, list[str], bool]  # (invocation, expected lines, expect-failure)


def parse_specs(text: str) -> list[Spec]:
    """(invocation, [expected, ...], expect_failure) cases from spec comments."""
    cases: list[Spec] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#?!"):
            invocation = stripped[3:].strip()
            if not invocation:
                raise ValueError("empty '#?!' invocation")
            cases.append((invocation, [], True))
        elif stripped.startswith("#?"):
            invocation = stripped[2:].strip()
            if not invocation:
                raise ValueError("empty '#?' invocation")
            if "=>" in invocation:
                raise ValueError(
                    f"put the expectation on its own '#=>' line: {stripped!r}"
                )
            cases.append((invocation, [], False))
        elif stripped.startswith("#=>"):
            if not cases:
                raise ValueError("#=> before any #? line")
            expected = stripped[3:].strip()
            if not expected:
                raise ValueError("empty '#=>' expectation line")
            cases[-1][1].append(expected)
    for invocation, expecteds, expect_failure in cases:
        if expect_failure and expecteds:
            raise ValueError(f"'#?!' spec {invocation!r} must not have #=> lines")
        if not expect_failure and not expecteds:
            raise ValueError(f"spec {invocation!r} has no expected output")
    return cases


# Pathological macro expansions can eat the whole host (an unguarded
# preprocessor bomb nearly OOMed a 15G machine): cap every cc invocation.
# The cap derives from the HOST's memory - an eighth of physical RAM,
# clamped to [256 MiB, 2 GiB] - so small machines stay safe and big ones
# don't fail legitimate runs.


def _host_mem_bytes() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError):
        return 8 << 30  # sensible default when the probe is unavailable


CPP_MEM_LIMIT_BYTES = max(256 << 20, min(2 << 30, _host_mem_bytes() // 8))
DEFAULT_TIMEOUT_S = 60.0


def _limit_cpp_resources() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (CPP_MEM_LIMIT_BYTES, CPP_MEM_LIMIT_BYTES))


class CppError(Exception):
    """Preprocessing failed, timed out, or blew its resource cap."""


def run_cpp(
    c_file: Path,
    *,
    cc: str,
    flags: Sequence[str] = (),
    timeout: float = DEFAULT_TIMEOUT_S,
) -> str:
    """Preprocess a C file; failures surface the compiler's stderr instead
    of an opaque CalledProcessError. Memory- and time-capped so runaway
    expansions fail the check instead of the machine."""
    cmd = [cc, "-E", "-P", *flags, str(c_file)]
    try:
        run = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_limit_cpp_resources,
        )
    except subprocess.TimeoutExpired:
        raise CppError(
            f"preprocessing timed out after {timeout:g}s: {' '.join(cmd)}"
        ) from None
    if run.returncode != 0:
        raise CppError(
            f"preprocessing failed: {' '.join(cmd)}\n--- compiler stderr ---\n{run.stderr}"
        )
    return run.stdout


@dataclass
class SpecResult:
    invocation: str
    ok: bool
    detail: str = ""  # expected/actual (or error text) when not ok


def check_file(
    path: Path,
    *,
    cc: str,
    cflags: Sequence[str] = (),
    timeout: float = DEFAULT_TIMEOUT_S,
    work_dir: Path | None = None,
) -> list[SpecResult]:
    """Run every spec in one template. Raises ValueError for malformed or
    absent specs and UncursedPpError when the template does not compile.

    With work_dir, all artifacts (header, runtime companion, one numbered
    snippet per spec) are written there and KEPT for debugging; otherwise
    a temp dir is used and cleaned up."""
    source = path.read_text()
    specs = parse_specs(source)
    if not specs:
        raise ValueError(f"{path}: no #? specs found")
    result = compile_template(source, path.name)
    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        return _run_specs(path, specs, result, work_dir, cc, cflags, timeout)
    with tempfile.TemporaryDirectory() as td:
        return _run_specs(path, specs, result, Path(td), cc, cflags, timeout)


def _run_specs(
    path: Path,
    specs: list[Spec],
    result: CompileResult,
    tmp: Path,
    cc: str,
    cflags: Sequence[str],
    timeout: float,
) -> list[SpecResult]:
    header = tmp / f"{path.stem}.h"
    header.write_text(result.header)
    if result.runtime is not None:
        (tmp / result.runtime_name).write_text(result.runtime)
    results: list[SpecResult] = []
    for n, (invocation, expecteds, expect_failure) in enumerate(specs, start=1):
        snippet = tmp / f"{path.stem}_spec_{n}.c"
        snippet.write_text(f'#include "{header.name}"\n{invocation}\n')
        try:
            out = canon(run_cpp(snippet, cc=cc, flags=cflags, timeout=timeout))
        except CppError as exc:
            if expect_failure:
                results.append(SpecResult(invocation, True))
            else:
                results.append(SpecResult(invocation, False, str(exc)))
            continue
        if expect_failure:
            results.append(
                SpecResult(
                    invocation, False, "expected preprocessing to FAIL, but it succeeded"
                )
            )
            continue
        expected = canon(" ".join(expecteds))
        if out == expected:
            results.append(SpecResult(invocation, True))
        else:
            results.append(
                SpecResult(invocation, False, f"expected: {expected}\nactual:   {out}")
            )
    return results


def _default_cc() -> str | None:
    return os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uncursed-pp-check",
        description=(
            "Verify a template's inline '#? INVOCATION' / '#=> expansion' "
            "specs through a real C preprocessor. The '#=>' lines are the "
            "COMPLETE expected expansion (token-exact, whitespace-"
            "canonicalized); '#?!' cases must fail preprocessing. "
            "Arguments after '--' are passed to the compiler verbatim "
            "(e.g. -I for Boost.Preprocessor)."
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help=(
            "input .uncursed template file(s) and/or directories "
            "(a directory is searched recursively for *.uncursed)"
        ),
    )
    parser.add_argument(
        "--cc", default=None, help="C compiler to use (default: $CC, else cc/gcc from PATH)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"per-invocation preprocessor timeout in seconds (default {DEFAULT_TIMEOUT_S:g})",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help=(
            "write the generated header, runtime companion and one numbered "
            ".c snippet per spec into this directory and KEEP them (for "
            "debugging; default: a cleaned-up temp dir)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cflags: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, cflags = argv[:split], argv[split + 1 :]
    args = build_arg_parser().parse_args(argv)

    cc = args.cc or _default_cc()
    if cc is None or shutil.which(cc) is None:
        print(
            f"uncursed-pp-check: C compiler not found: {cc or '(no cc/gcc on PATH)'}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    paths: list[Path] = []
    for name in args.inputs:
        p = Path(name)
        if p.is_dir():
            found = sorted(p.rglob("*.uncursed"))
            if not found:
                print(
                    f"uncursed-pp-check: no .uncursed templates under {p}", file=sys.stderr
                )
                raise SystemExit(2)
            paths.extend(found)
        else:
            paths.append(p)

    total_passed = 0
    total_specs = 0
    failed_files: list[tuple[Path, int, int]] = []  # (path, passed, total)
    for n, path in enumerate(paths):
        if n:
            print()  # blank line between file sections
        try:
            results = check_file(
                path,
                cc=cc,
                cflags=cflags,
                timeout=args.timeout,
                work_dir=Path(args.work_dir) if args.work_dir else None,
            )
        except OSError as exc:
            print(f"uncursed-pp-check: cannot read {path}: {exc.strerror}", file=sys.stderr)
            raise SystemExit(2) from exc
        except (ValueError, UncursedPpError) as exc:
            print(f"uncursed-pp-check: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        passed = sum(1 for r in results if r.ok)
        for r in results:
            print(f"{'ok  ' if r.ok else 'FAIL'}  {r.invocation}")
            if not r.ok:
                for line in r.detail.splitlines():
                    print(f"      {line}")
        print(f"{path}: {passed}/{len(results)} specs passed")
        total_passed += passed
        total_specs += len(results)
        if passed != len(results):
            failed_files.append((path, passed, len(results)))
    if len(paths) > 1:
        print()
        print(f"{len(paths)} files: {total_passed}/{total_specs} specs passed")
        for path, passed, total in failed_files:
            print(f"FAILED {path} ({passed}/{total})")
    raise SystemExit(1 if failed_files else 0)


if __name__ == "__main__":
    main()
