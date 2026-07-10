import argparse
import sys
from pathlib import Path

from .emitter import compile_template
from .parser import CursedppError


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cursedpp",
        description=(
            "Compile a .cursed template into a Boost.Preprocessor C header. "
            "Configuration (pp_prefix, pp_include, pp_include_dir, "
            "helper_prefix, runtime_name, include) lives in the template "
            "itself via @pragma lines, so output is reproducible from the "
            "source file alone."
        ),
    )
    parser.add_argument("input", help="input .cursed template file")
    parser.add_argument("-o", "--output", help="output header path (default: <input stem>.h)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path.with_suffix(".h")
    if output_path.resolve() == input_path.resolve():
        print(
            f"cursedpp: refusing to overwrite the input file {input_path} "
            "(pass -o with a different output path)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        result = compile_template(input_path.read_text(), str(input_path))
    except CursedppError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    output_path.write_text(result.header)
    if result.runtime is not None:
        (output_path.parent / result.runtime_name).write_text(result.runtime)


if __name__ == "__main__":
    main()
