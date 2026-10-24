"""Command-line entry: `python -m devtools …` or the shims in bin/."""
import argparse
import sys

from devtools import __version__

USAGE_ERROR = 5  # argparse's own 2 would collide with "bridge unreachable"


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(USAGE_ERROR)


def build_parser() -> Parser:
    parser = Parser(prog="devtools")
    parser.add_argument("--version", action="version", version=f"devtools {__version__}")
    tools = parser.add_subparsers(dest="tool", parser_class=Parser)
    from devtools import fusion
    fusion.add_parser(tools)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return USAGE_ERROR
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
