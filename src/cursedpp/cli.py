import argparse


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cursedpp",
        description="Compile a .cursed template into a Boost.Preprocessor C header.",
    )
    parser.add_argument("input", help="input .cursed template file")
    parser.add_argument("-o", "--output", help="output header path (default: <input stem>.h)")
    return parser


def main(argv: list[str] | None = None) -> None:
    build_arg_parser().parse_args(argv)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
