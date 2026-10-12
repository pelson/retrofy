"""retrofy builds itself with the build hooks in its own source tree.

Declaring the hooks inline (rather than relying on an installed retrofy's
entry points) means a build always uses the checkout's hooks, so retrofy can
use a new hook (e.g. ``post-load-backend`` for its dynamic requires-python)
without first releasing it.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest

import retrofy
from retrofy._pep517_hooks import compatibility_via_rewrite

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

PACKAGE_DIR = pathlib.Path(retrofy.__file__).parent
PYPROJECT = PACKAGE_DIR.parent / "pyproject.toml"


@pytest.fixture
def pyproject() -> dict:
    if not PYPROJECT.exists():
        pytest.skip("needs retrofy's source tree (not an installed wheel)")
    return tomllib.loads(PYPROJECT.read_text())


def test_build_hooks_declared_inline(pyproject):
    entry_points = pyproject["project"]["entry-points"]["multistage_build"]
    inline = {
        name: hooks
        for name, hooks in pyproject["tool"]["multistage-build"].items()
        if name != "build-backend"
    }
    assert {
        name: [{"hook-function": value, "hook-path": "."}]
        for name, value in entry_points.items()
    } == inline


def test_build_does_not_need_an_installed_retrofy(pyproject):
    requires = pyproject["build-system"]["requires"]
    assert not any(r.split()[0].startswith("retrofy") for r in requires)
    assert "requires-python" in pyproject["project"]["dynamic"]
    assert "requires-python" not in pyproject["project"]


def test_hooks_importable_before_version_file_exists(tmp_path):
    # In a fresh checkout setuptools-scm writes _version.py during the build,
    # after the inline hooks have imported retrofy.
    shutil.copytree(
        PACKAGE_DIR,
        tmp_path / "retrofy",
        ignore=shutil.ignore_patterns("_version.py", "tests", "__pycache__"),
    )
    subprocess.run(
        [sys.executable, "-c", "import retrofy._pep517_hooks"],
        cwd=tmp_path,
        check=True,
    )


def test_wheel_hook_is_idempotent(tmp_path, monkeypatch):
    # A hook can be loaded both inline and through an installed retrofy's
    # entry point (e.g. a --no-isolation build in an env with retrofy), so
    # it may run twice: the second run must leave the wheel alone (#79).
    monkeypatch.chdir(tmp_path)
    whl = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr(
            "demo/__init__.py",
            '"""Doc."""\nimport sys\nfrom collections.abc import Mapping\n',
        )
        z.writestr("demo-1.0.dist-info/METADATA", "Name: demo\nVersion: 1.0\n")
        z.writestr("demo-1.0.dist-info/RECORD", "")
    compatibility_via_rewrite(whl)
    first = whl.read_bytes()
    compatibility_via_rewrite(whl)
    assert whl.read_bytes() == first


def test_build_requires_what_the_hooks_import(pyproject):
    # The inline hooks import retrofy from the checkout, so retrofy's runtime
    # dependencies must be available in the build environment.
    build_requires = set(pyproject["build-system"]["requires"])
    missing = set(pyproject["project"]["dependencies"]) - build_requires
    assert not missing
