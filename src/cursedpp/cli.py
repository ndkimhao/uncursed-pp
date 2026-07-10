import argparse
import sys
from pathlib import Path

from .emitter import EmitConfig, compile_template
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
    parser.add_argument(
        "--runtime-name",
        default=None,
        help="filename of the shared runtime header (default: derived from --helper-prefix)",
    )
    parser.add_argument(
        "--pp-include-dir",
        default="boost/preprocessor",
        help="root of the granular preprocessor includes (default: boost/preprocessor)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="HEADER",
        help='extra #include for generated headers (repeatable; "<...>" for angle form)',
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
        runtime_name=args.runtime_name,
        pp_include_dir=args.pp_include_dir,
        extra_includes=tuple(args.include),
    )
    try:
        result = compile_template(input_path.read_text(), str(input_path), config=config)
    except CursedppError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    output_path.write_text(result.header)
    if result.runtime is not None:
        (output_path.parent / result.runtime_name).write_text(result.runtime)


if __name__ == "__main__":
    main()
