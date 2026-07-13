"""Regression coverage for https://github.com/pelson/retrofy/issues/45.

We drive the full converter over an inline source snippet that
mirrors the issue's reproducer, then shell out to mypy on the output.
mypy must accept the converted source and infer the real types of
lazy-bound names — not ``LazyProxy``, not ``Any``, and no
``[valid-type]`` rejection on the annotation slot.

Skipped when mypy is not on PATH.
"""

import pathlib
import shutil
import subprocess
import sys
import textwrap

import pytest

from retrofy._converters import convert
import retrofy._retrofy_rt as _retrofy_rt

_LAZY_SOURCE = textwrap.dedent(
    '''\
    """Fixture for the mypy regression check on lazy-import annotations."""

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
    ''',
)


@pytest.fixture(scope="module")
def mypy_bin() -> str:
    which = shutil.which("mypy")
    if which is None:
        return pytest.skip("mypy is not installed")
    return which


def _write_converted_package(tmp_path: pathlib.Path) -> pathlib.Path:
    """Convert the fixture snippet and drop it into a package tree with
    a real copy of ``_retrofy_rt/lazy_imports.py`` alongside so mypy
    can resolve the helper imports the converter emits.
    """
    converted = convert(_LAZY_SOURCE)

    pkg = tmp_path / "example_project"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "typed_lazy.py").write_text(converted)

    rt = pkg / "_retrofy_rt"
    rt.mkdir()
    (rt / "__init__.py").write_text("")
    rt_src = pathlib.Path(_retrofy_rt.__file__).parent / "lazy_imports.py"
    shutil.copy(rt_src, rt / "lazy_imports.py")
    return pkg


def _write_mypy_config(tmp_path: pathlib.Path) -> pathlib.Path:
    cfg = tmp_path / "mypy.ini"
    cfg.write_text(
        textwrap.dedent(
            """
            [mypy]
            python_version = 3.9
            strict = True
            """,
        ).lstrip(),
    )
    return cfg


def test_mypy_accepts_converted_lazy_annotations(
    tmp_path: pathlib.Path,
    mypy_bin: str,
) -> None:
    pkg = _write_converted_package(tmp_path)
    cfg = _write_mypy_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(cfg),
            str(pkg / "typed_lazy.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "mypy rejected the converted form of the lazy-annotations fixture:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_runtime_get_type_hints_returns_real_class(tmp_path: pathlib.Path) -> None:
    """The converted output must round-trip through ``typing.get_type_hints``
    and return the *real* lazy-bound class — matching native PEP 810's
    "``foo.__annotations__`` access triggers reification" semantic.

    Previous shapes of the fix regressed this: they either left a
    ``LazyProxy`` in the annotation slot or made ``get_type_hints``
    raise ``TypeError: Forward references must evaluate to types``.
    """
    pkg = _write_converted_package(tmp_path)

    # Execute the converted module in a fresh subprocess so this test
    # doesn't leak imports into pytest's own interpreter, and drive
    # ``typing.get_type_hints`` on each defined function.
    driver = pkg.parent / "driver.py"
    driver.write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(pkg.parent)!r})
            import typing
            import pathlib
            import collections.abc
            import example_project.typed_lazy as m

            hints = typing.get_type_hints(m.make_path)
            assert hints['return'] is pathlib.Path, (
                f'expected pathlib.Path, got {{hints["return"]!r}}'
            )
            assert hints['name'] is str

            hints = typing.get_type_hints(m.annotate_optional)
            # ``Optional[Path]`` normalises to ``Path | None``; the
            # return is a bare ``Path``.
            assert hints['return'] is pathlib.Path

            hints = typing.get_type_hints(m.check_mapping)
            # ``Mapping[str, int]`` — origin should be the real ABC.
            m_hint = hints['m']
            assert typing.get_origin(m_hint) is collections.abc.Mapping, (
                f'origin was {{typing.get_origin(m_hint)!r}}'
            )
            assert typing.get_args(m_hint) == (str, int)

            print('OK')
            """,
        ).lstrip(),
    )
    result = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "runtime introspection of converted typed_lazy failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().endswith("OK")
