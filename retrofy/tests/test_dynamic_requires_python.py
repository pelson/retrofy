"""A dynamic ``requires-python``, supplied by retrofy from ``[tool.retrofy]
target-python``.

setuptools only accepts a dynamic ``requires-python`` from ``setup.py``, and
rejects it already in ``get_requires_for_build_*``, the first call any
frontend makes. multistage-build's ``post-load-backend`` hook runs before
every call, and retrofy uses it to supply the value in memory.
"""

from __future__ import annotations

import importlib.metadata
import pathlib
import textwrap
import types

import pytest
from setuptools import build_meta
import setuptools.config.pyprojecttoml as pyprojecttoml

from retrofy._pep517_hooks import supply_dynamic_requires_python

DYNAMIC_PYPROJECT = """\
    [build-system]
    requires = ["multistage-build>=0.4", "setuptools", "retrofy"]
    build-backend = "multistage_build:backend"

    [tool.multistage-build]
    build-backend = "setuptools.build_meta"

    [project]
    name = "dynamic-rpy"
    version = "0.1"
    dynamic = ["requires-python"]

    [tool.setuptools]
    py-modules = []
    """


@pytest.fixture
def project(tmp_path, monkeypatch):
    # Restore setuptools' loader after each test: the hook replaces it for the
    # rest of the (normally single-call) build process.
    monkeypatch.setattr(pyprojecttoml, "load_file", pyprojecttoml.load_file)
    monkeypatch.chdir(tmp_path)

    def write(body: str) -> pathlib.Path:
        (tmp_path / "pyproject.toml").write_text(textwrap.dedent(body))
        return tmp_path

    return write


def test_setuptools_builds_with_dynamic_requires_python(project, tmp_path):
    project(DYNAMIC_PYPROJECT + '[tool.retrofy]\ntarget-python = "3.9"\n')
    supply_dynamic_requires_python(build_meta)

    # The first call any frontend makes, which setuptools would reject.
    build_meta.get_requires_for_build_wheel()
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    dist_info = build_meta.prepare_metadata_for_build_wheel(str(metadata_dir))
    metadata = (metadata_dir / dist_info / "METADATA").read_text()
    assert "Requires-Python: >=3.9" in metadata


def test_pyproject_on_disk_untouched(project, tmp_path):
    source = DYNAMIC_PYPROJECT + '[tool.retrofy]\ntarget-python = "3.9"\n'
    project(source)
    supply_dynamic_requires_python(build_meta)
    build_meta.get_requires_for_build_wheel()
    assert (tmp_path / "pyproject.toml").read_text() == textwrap.dedent(source)


def test_requires_target_python(project):
    project(DYNAMIC_PYPROJECT)
    with pytest.raises(ValueError, match=r"\[tool.retrofy\] target-python"):
        supply_dynamic_requires_python(build_meta)


@pytest.mark.parametrize(
    "case",
    ["static-requires-python", "other-backend", "other-pyproject"],
)
def test_left_alone(project, tmp_path, case):
    original = pyprojecttoml.load_file
    if case == "static-requires-python":
        project(
            DYNAMIC_PYPROJECT.replace(
                'dynamic = ["requires-python"]',
                'requires-python = ">=3.15"',
            )
            + '[tool.retrofy]\ntarget-python = "3.9"\n',
        )
        supply_dynamic_requires_python(build_meta)
        assert pyprojecttoml.load_file is original
    elif case == "other-backend":
        project(DYNAMIC_PYPROJECT + '[tool.retrofy]\ntarget-python = "3.9"\n')
        supply_dynamic_requires_python(types.ModuleType("hatchling.build"))
        assert pyprojecttoml.load_file is original
    else:
        project(DYNAMIC_PYPROJECT + '[tool.retrofy]\ntarget-python = "3.9"\n')
        supply_dynamic_requires_python(build_meta)
        other = tmp_path / "other"
        other.mkdir()
        (other / "pyproject.toml").write_text(textwrap.dedent(DYNAMIC_PYPROJECT))
        data = pyprojecttoml.load_file(other / "pyproject.toml")
        assert "requires-python" in data["project"]["dynamic"]
        assert "requires-python" not in data["project"]


def test_registered_as_post_load_backend_hook():
    entry_points = importlib.metadata.entry_points()
    if hasattr(entry_points, "select"):
        group = entry_points.select(group="multistage_build")
    else:  # Python < 3.10: a dict of groups.
        group = entry_points.get("multistage_build", [])
    # A set: Python < 3.10 lists a distribution once per sys.path entry that
    # finds it.
    values = {ep.value for ep in group if ep.name == "post-load-backend"}
    assert values == {"retrofy._pep517_hooks:supply_dynamic_requires_python"}


def test_wraps_the_loader_once(project):
    project(DYNAMIC_PYPROJECT + '[tool.retrofy]\ntarget-python = "3.9"\n')
    supply_dynamic_requires_python(build_meta)
    wrapped = pyprojecttoml.load_file
    supply_dynamic_requires_python(build_meta)
    assert pyprojecttoml.load_file is wrapped
