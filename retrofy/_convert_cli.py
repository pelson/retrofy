"""``retrofy wheel`` and ``retrofy files``: convert an existing wheel, or a
directory of Python files, into a copy that runs on older Pythons.

Prefer converting in the project's own build (the ``[tool.retrofy]`` build
hooks), where the result can be tested on the target Python as part of the
project's CI. These commands are for code whose build you don't control.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
from typing import Sequence

from packaging.utils import InvalidWheelFilename, parse_wheel_filename

from ._pep517_hooks import (
    _convert_directory,
    _convert_wheel,
    _lower_requires_python,
    _splice_typing_extensions_requires_dist,
    _validate_target_python,
)

_TEST_REMINDER = (
    "retrofy can't check the converted code's behaviour: test it on "
    "Python {floor} before relying on it."
)


def wheel_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="retrofy wheel",
        description=(
            "Write a copy of a pure-Python wheel whose code runs on an older "
            "Python, with Requires-Python lowered to match. The name and "
            "version are kept."
        ),
    )
    parser.add_argument("wheel", type=Path, help="the wheel to convert")
    parser.add_argument(
        "--target-python",
        required=True,
        help='the oldest Python to support, e.g. "3.9"',
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="directory to write the converted wheel to",
    )
    args = parser.parse_args(argv)

    try:
        floor = _validate_target_python(args.target_python, "--target-python")
    except TypeError, ValueError:
        parser.error(
            f'--target-python must be a single version like "3.9", '
            f"got {args.target_python!r}",
        )
    source: Path = args.wheel
    if not source.is_file():
        parser.error(f"{source} does not exist")
    try:
        _, _, _, tags = parse_wheel_filename(source.name)
    except InvalidWheelFilename as exc:
        parser.error(str(exc))
    if not all(
        tag.interpreter in {"py2", "py3"}
        and tag.abi == "none"
        and tag.platform == "any"
        for tag in tags
    ):
        parser.error(
            f"{source.name} is not a pure-Python wheel (py3-none-any); "
            f"only those can be converted",
        )
    destination = args.output_dir / source.name
    if destination.resolve() == source.resolve():
        parser.error(
            f"writing to {args.output_dir} would overwrite the input wheel; "
            f"choose another --output-dir",
        )

    def edit_metadata(text: str) -> str:
        text = _lower_requires_python(text, floor)
        return _splice_typing_extensions_requires_dist(text, floor)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not _convert_wheel(source, destination, edit_metadata):
        shutil.copy(source, destination)
    print(f"Wrote {destination}")
    print(_TEST_REMINDER.format(floor=floor))
    return 0


def files_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="retrofy files",
        description=(
            "Write a copy of a directory with its Python files converted to "
            "run on older Pythons. Other files are copied as they are."
        ),
    )
    parser.add_argument("source", type=Path, help="the directory to convert")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="directory to write the converted copy to (must be empty)",
    )
    args = parser.parse_args(argv)

    source: Path = args.source
    output: Path = args.output_dir
    if not source.is_dir():
        parser.error(f"{source} is not a directory")
    if output.resolve().is_relative_to(source.resolve()):
        parser.error(f"the output directory {output} must not be inside {source}")
    if output.exists() and any(output.iterdir()):
        parser.error(f"the output directory {output} is not empty")

    shutil.copytree(
        source,
        output,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )
    _convert_directory(output)
    print(f"Wrote {output}")
    print(_TEST_REMINDER.format(floor="versions you target"))
    return 0
