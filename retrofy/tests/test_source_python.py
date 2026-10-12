"""``[tool.retrofy] source-python``: the Python the on-disk source runs on
unconverted.

With ``requires-python`` describing what the built artefacts run on, the
source's own requirement is declared separately. It doesn't limit which
Python builds the project: retrofy's hooks only parse the source (CI builds
the 3.15 example project with Python 3.13).
"""

from __future__ import annotations

import pathlib
import sys
import textwrap

import pytest
from setuptools import build_meta
import setuptools.config.pyprojecttoml as pyprojecttoml

import retrofy
from retrofy._pep517_hooks import _patch_pyproject_for_lowered_sdist, post_load_backend

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

PROJECT = """\
    [project]
    name = "demo"
    version = "0.1"
    dynamic = ["requires-python"]

    [tool.retrofy]
    target-python = "3.9"
    """


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(pyprojecttoml, "load_file", pyprojecttoml.load_file)
    monkeypatch.chdir(tmp_path)

    def write(source_python: str) -> None:
        (tmp_path / "pyproject.toml").write_text(
            textwrap.dedent(PROJECT) + f'source-python = "{source_python}"\n',
        )

    return write


def test_valid_source_python(project):
    project("3.15")
    post_load_backend(build_meta)


def test_source_python_must_be_a_version(project):
    project(">=3.15")
    with pytest.raises(ValueError, match=r"\[tool.retrofy\] source-python"):
        post_load_backend(build_meta)


def test_target_python_not_newer_than_source_python(project):
    project("3.8")  # target-python is 3.9
    with pytest.raises(ValueError, match="target-python"):
        post_load_backend(build_meta)


def test_lowered_sdist_drops_source_python():
    text = textwrap.dedent(PROJECT) + 'source-python = "3.15"\n'
    lowered = tomllib.loads(_patch_pyproject_for_lowered_sdist(text, "3.9"))
    # The sdist's source is already lowered, and its requires-python static.
    assert lowered["project"]["requires-python"] == ">=3.9"
    assert "source-python" not in lowered["tool"]["retrofy"]
    assert "target-python" not in lowered["tool"]["retrofy"]


@pytest.mark.parametrize("path", [".", "example-project"])
def test_source_python_declared(path):
    pyproject = pathlib.Path(retrofy.__file__).parents[1] / path / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("needs retrofy's source tree (not an installed wheel)")
    config = tomllib.loads(pyproject.read_text())
    assert config["tool"]["retrofy"]["source-python"] == "3.15"
