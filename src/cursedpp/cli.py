import argparse
import sys
from pathlib import Path

from .emitter import EmitConfig, compile_source
from .parser import CursedppError


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cursedpp",
        description="Compile a .cursed template into a Boost.Preprocessor C header.",
    )
    parser.add_argument("input", help="input .cursed template file")
    parser.add_argument("-o", "--output", help="output header path (default: <input stem>.h)")
    parser.add_argument(
        "--pp-prefix",
        default="BOOST_PP_",
        help="prefix of the preprocessor library's macros (default: BOOST_PP_)",
    )
    parser.add_argument(
        "--pp-include",
        default=None,
        help="header to #include (default: granular boost/preprocessor/*.hpp by usage)",
    )
    parser.add_argument(
        "--helper-prefix",
        default="CURSEDPP_",
        help="prefix for generated helper macros (default: CURSEDPP_)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path.with_suffix(".h")
    config = EmitConfig(
        pp_prefix=args.pp_prefix,
        pp_include=args.pp_include,
        helper_prefix=args.helper_prefix,
    )
    try:
        header = compile_source(input_path.read_text(), str(input_path), config=config)
    except CursedppError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    output_path.write_text(header)


if __name__ == "__main__":
    main()
