import argparse
import sys
from pathlib import Path

from .emitter import compile_source
from .parser import CursedppError


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cursedpp",
        description="Compile a .cursed template into a Boost.Preprocessor C header.",
    )
    parser.add_argument("input", help="input .cursed template file")
    parser.add_argument("-o", "--output", help="output header path (default: <input stem>.h)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path.with_suffix(".h")
    try:
        header = compile_source(input_path.read_text(), str(input_path))
    except CursedppError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    output_path.write_text(header)


if __name__ == "__main__":
    main()
