"""Regression coverage for issue #45.

The lazy-imports rewriter must produce converted source that mypy can
type-check when the lazy-bound name is used in:

* function parameter and return annotations
* module-level variable annotations
* runtime call sites

We type-check the *converted* form of this module (as it lands in the
built wheel), not the on-disk pre-conversion source (which contains
Python 3.15+ ``lazy`` soft-keyword syntax that mypy does not yet
parse).
"""

from __future__ import annotations

import typing

lazy from pathlib import Path
lazy from collections.abc import Mapping


def make_path(name: str) -> Path:
    return Path(name)


def annotate_optional(p: typing.Optional[Path]) -> Path:
    if p is None:
        return Path(".")
    return p


def check_mapping(m: Mapping[str, int]) -> int:
    return sum(m.values())


module_level: Path = Path("/tmp")
