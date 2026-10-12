from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import _setup_editable
lazy from ._convert_cli import files_main, wheel_main


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retrofy")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup-editable", add_help=False)
    sub.add_parser("wheel", add_help=False)
    sub.add_parser("files", add_help=False)

    args, remainder = parser.parse_known_args(argv)
    if args.cmd == "setup-editable":
        return _setup_editable.main(remainder)
    if args.cmd == "wheel":
        return wheel_main(remainder)
    if args.cmd == "files":
        return files_main(remainder)
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
