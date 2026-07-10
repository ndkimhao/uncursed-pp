import argparse
import sys
from pathlib import Path

from .emitter import compile_template, runtime_header, runtime_include_text
from .parser import UncursedPpError


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uncursed-pp",
        description=(
            "Compile a .uncursed template into a Boost.Preprocessor C header. "
            "Configuration (pp_prefix, pp_include, pp_include_dir, "
            "helper_prefix, runtime_name, include) lives in the template "
            "itself via @pragma lines, so output is reproducible from the "
            "source file alone."
        ),
    )
    parser.add_argument("input", help="input .uncursed template file")
    parser.add_argument("-o", "--output", help="output header path (default: <input stem>.h)")
    parser.add_argument(
        "--emit-runtime",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "write the shared companion runtime header next to the output. "
            "By default it is never written: a header that needs one gets a "
            "stderr note instead (silence it with --no-emit-runtime)"
        ),
    )
    parser.add_argument(
        "--runtime-chain-limits",
        type=_chain_limits,
        default=(),
        metavar="K[,K...]",
        help=(
            "extra loop_chain_limit values whose LE<K> size-class tables the "
            "written runtime should carry, so one shared file serves headers "
            "generated with different limits (merged with this template's "
            "own needs and the default 16; only meaningful with "
            "--emit-runtime)"
        ),
    )
    return parser


def _chain_limits(value: str) -> tuple[int, ...]:
    try:
        limits = tuple(int(part) for part in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a comma-separated int list: {value!r}")
    for k in limits:
        if not 1 <= k <= 256:
            raise argparse.ArgumentTypeError(f"chain limit {k} outside 1..256")
    return limits


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path.with_suffix(".h")
    if output_path.resolve() == input_path.resolve():
        print(
            f"uncursed-pp: refusing to overwrite the input file {input_path} "
            "(pass -o with a different output path)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        source = input_path.read_text()
    except OSError as exc:
        print(f"uncursed-pp: cannot read {input_path}: {exc.strerror}", file=sys.stderr)
        raise SystemExit(1) from exc
    try:
        result = compile_template(source, str(input_path))
    except UncursedPpError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    try:
        output_path.write_text(result.header)
        if args.emit_runtime:
            # explicit request: write even when this header doesn't need it,
            # so one invocation can seed a directory shared by many headers.
            # Tables: this template's needs + the default + any CLI extras.
            limits = sorted({16, *result.chain_limits, *args.runtime_chain_limits})
            runtime = runtime_header(result.config, chain_limits=limits)
            (output_path.parent / result.runtime_name).write_text(runtime)
    except OSError as exc:
        print(f"uncursed-pp: cannot write {output_path}: {exc.strerror}", file=sys.stderr)
        raise SystemExit(1) from exc
    if args.emit_runtime is None and result.runtime is not None:
        print(
            f"uncursed-pp: note: {output_path.name} includes {runtime_include_text(result.config)}; "
            "pass --emit-runtime to write it (or --no-emit-runtime to silence this note)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
