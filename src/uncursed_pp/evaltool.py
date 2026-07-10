"""`uncursed-pp-eval`: expand one invocation of one template through the
real C preprocessor - and fill `<???>` spec sentinels with the result.

The workflow the sentinel enables: write the invocation, leave the
expectation as a single `#=> <???>` line, run
`uncursed-pp-eval file.uncursed --update-specs`, review the diff. The
`#=>` convention (complete, token-exact expansions) is unchanged - the
sentinel is the explicit request to transcribe reality.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from .emitter import compile_template
from .parser import UncursedPpError
from .speccheck import (
    DEFAULT_TIMEOUT_S,
    CppError,
    _default_cc,
    parse_specs,
    run_cpp,
)

SENTINEL = "<???>"


def _find_clang_format() -> str | None:
    return os.environ.get("CLANG_FORMAT") or shutil.which("clang-format")


def _clang_format(
    text: str, clang_format: str, timeout: float, extra: Sequence[str] = ()
) -> str:
    run = subprocess.run(
        [clang_format, *extra],
        input=text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if run.returncode != 0:
        raise CppError(f"clang-format failed:\n{run.stderr}")
    return run.stdout


class _Evaluator:
    """One compiled template, many invocations."""

    def __init__(
        self,
        path: Path,
        tmp: Path,
        *,
        cc: str,
        cflags: Sequence[str],
        timeout: float,
    ):
        self.tmp = tmp
        self.cc = cc
        self.cflags = cflags
        self.timeout = timeout
        result = compile_template(path.read_text(), path.name)
        self.header = tmp / f"{path.stem}.h"
        self.header.write_text(result.header)
        if result.runtime is not None:
            (tmp / result.runtime_name).write_text(result.runtime)

    def expand(self, invocation: str) -> str:
        """RAW expansion of one invocation (blank lines dropped). Raw, not
        canon: canon is for token comparison and splits nothing visually -
        output meant for humans, clang-format, and spec filling must keep
        the compiler's own text (`<<` stays `<<`)."""
        snippet = self.tmp / "eval.c"
        snippet.write_text(f'#include "{self.header.name}"\n{invocation}\n')
        out = run_cpp(snippet, cc=self.cc, flags=self.cflags, timeout=self.timeout)
        lines = [line.rstrip() for line in out.splitlines() if line.strip()]
        return "\n".join(lines)


def _fill_specs(
    path: Path,
    ev: _Evaluator,
    *,
    fmt: str | None,
    fmt_args: Sequence[str] = (),
    timeout: float,
) -> tuple[int, list[str]]:
    """Replace every single-`<???>` expectation with the real expansion.
    Returns (filled count, error messages)."""
    crlf = b"\r\n" in path.read_bytes()  # preserve the file's line endings
    lines = path.read_text().split("\n")
    out: list[str] = []
    errors: list[str] = []
    filled = 0
    current_invocation: str | None = None
    expectation_lines_seen = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#?!"):
            # expect-failure specs never have expectations to fill
            current_invocation = None
            out.append(line)
            continue
        if stripped.startswith("#?"):
            current_invocation = stripped[2:].strip()
            expectation_lines_seen = 0
            out.append(line)
            continue
        if stripped.startswith("#=>"):
            expectation_lines_seen += 1
            is_lone_sentinel = (
                stripped[3:].strip() == SENTINEL
                and expectation_lines_seen == 1
                and (i + 1 >= len(lines) or not lines[i + 1].strip().startswith("#=>"))
            )
            if is_lone_sentinel and current_invocation:
                try:
                    expansion = ev.expand(current_invocation)
                except CppError as exc:
                    errors.append(f"{current_invocation}: {exc}")
                    out.append(line)
                    continue
                text = expansion
                if fmt is not None:
                    text = _clang_format(expansion, fmt, timeout, fmt_args).rstrip("\n")
                # blank lines (empty expansion, clang-format spacing) would
                # write bare '#=>' lines the checker rejects
                fill_lines = [l for l in text.split("\n") if l.strip()]
                if not fill_lines:
                    errors.append(
                        f"{current_invocation}: expansion is empty - wrap the "
                        f"invocation in anchor text (e.g. 'begin "
                        f"{current_invocation} end') so the expectation has "
                        "tokens, then re-run"
                    )
                    out.append(line)
                    continue
                for expanded_line in fill_lines:
                    out.append(f"#=>     {expanded_line}")
                filled += 1
                continue
            out.append(line)
            continue
        out.append(line)
    if filled:
        path.write_text("\n".join(out), newline="\r\n" if crlf else "")
    return filled, errors


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uncursed-pp-eval",
        description=(
            "Expand one invocation of a template through the real C "
            "preprocessor, or fill '#=> <???>' spec sentinels with real "
            "expansions (--update-specs). Arguments after '--' go to the "
            "compiler verbatim (e.g. -I for Boost.Preprocessor)."
        ),
    )
    parser.add_argument("input", help="the .uncursed template file")
    parser.add_argument(
        "invocation", nargs="?", default=None, help="macro invocation to expand and print"
    )
    parser.add_argument(
        "--cc", default=None, help="C compiler (default: $CC, else cc/gcc from PATH)"
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S,
        help=f"preprocessor timeout in seconds (default {DEFAULT_TIMEOUT_S:g})",
    )
    parser.add_argument(
        "--format", action="store_true",
        help="pretty-print through clang-format ($CLANG_FORMAT or PATH)",
    )
    parser.add_argument(
        "--format-arg", action="append", default=[], metavar="ARG",
        help=(
            "extra argument passed to clang-format verbatim (repeatable), "
            "e.g. --format-arg=-style=Google"
        ),
    )
    parser.add_argument(
        "--update-specs", action="store_true",
        help=(
            "rewrite the file in place: every spec whose expectation is a "
            f"single '#=> {SENTINEL}' line gets the real expansion"
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

    if (args.invocation is None) == (not args.update_specs):
        print(
            "uncursed-pp-eval: pass exactly one of INVOCATION or --update-specs",
            file=sys.stderr,
        )
        raise SystemExit(2)
    cc = args.cc or _default_cc()
    if cc is None or shutil.which(cc) is None:
        print(
            f"uncursed-pp-eval: C compiler not found: {cc or '(no cc/gcc on PATH)'}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    fmt: str | None = None
    if args.format:
        fmt = _find_clang_format()
        if fmt is None:
            print(
                "uncursed-pp-eval: --format needs clang-format on PATH (or $CLANG_FORMAT)",
                file=sys.stderr,
            )
            raise SystemExit(2)

    path = Path(args.input)
    with tempfile.TemporaryDirectory() as td:
        try:
            ev = _Evaluator(
                path, Path(td), cc=cc, cflags=cflags, timeout=args.timeout
            )
        except OSError as exc:
            print(f"uncursed-pp-eval: cannot read {path}: {exc.strerror}", file=sys.stderr)
            raise SystemExit(2) from exc
        except UncursedPpError as exc:
            print(f"uncursed-pp-eval: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc

        if args.invocation is not None:
            try:
                expansion = ev.expand(args.invocation)
            except CppError as exc:
                print(f"uncursed-pp-eval: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc
            if fmt is not None:
                try:
                    print(_clang_format(expansion, fmt, args.timeout, args.format_arg), end="")
                except CppError as exc:
                    print(f"uncursed-pp-eval: {exc}", file=sys.stderr)
                    raise SystemExit(2) from exc
            else:
                print(expansion)
            raise SystemExit(0)

        try:
            # a malformed spec file must not be rewritten: filling from it
            # can attach expansions to the wrong invocation
            parse_specs(path.read_text())
        except ValueError as exc:
            print(f"uncursed-pp-eval: {path}: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        try:
            filled, errors = _fill_specs(
                path, ev, fmt=fmt, fmt_args=args.format_arg, timeout=args.timeout
            )
        except CppError as exc:  # clang-format problems are setup errors
            print(f"uncursed-pp-eval: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        for e in errors:
            print(f"uncursed-pp-eval: {e}", file=sys.stderr)
        if filled:
            print(f"{path}: filled {filled} spec(s)")
        else:
            print(f"{path}: nothing to fill (no lone '#=> {SENTINEL}' expectations)")
        raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
