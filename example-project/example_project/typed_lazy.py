"""Typed helpers that lazy-import their dependencies.

Everything the module needs from ``pathlib`` and ``collections.abc``
is declared with ``lazy from`` — the modules aren't loaded until one
of these functions is actually called (or the annotations are
introspected). Static type checkers see the lazy-bound names as their
real types, so ``mypy`` / ``pyright`` / IDEs give you completion and
error checking as if the imports were eager.

This module is part of example-project's ``mypy`` job — see
``[tool.mypy]`` in ``example-project/pyproject.toml`` — so the typed
lazy-imports story is verified end-to-end on every build.
"""

from __future__ import annotations

import typing

lazy from pathlib import Path
lazy from collections.abc import Mapping


def make_path(name: str) -> Path:
    """Build a :class:`pathlib.Path` from a string."""
    return Path(name)


def annotate_optional(p: typing.Optional[Path]) -> Path:
    """Normalise ``None`` to the current directory."""
    if p is None:
        return Path(".")
    return p


def check_mapping(m: Mapping[str, int]) -> int:
    """Sum the values of a string→int mapping."""
    return sum(m.values())


default_workdir: Path = Path("/tmp")
