"""With ``[tool.retrofy] target-python`` set, ``requires-python`` must be
dynamic: retrofy supplies it (see test_dynamic_requires_python.py).

retrofy used to lower a *static* ``requires-python`` in the built metadata,
but a build backend changing a static field deviates from PEP 621, and tools
that read pyproject.toml directly saw the source's floor. Every build hook
now rejects that configuration with migration instructions, and none of
them rewrites Requires-Python any more.
"""

from __future__ import annotations

import io
import pathlib
import tarfile
import textwrap
import zipfile

import pytest
from setuptools import build_meta
import setuptools.config.pyprojecttoml as pyprojecttoml

from retrofy._pep517_hooks import (
    compatibility_via_import_hook,
    compatibility_via_rewrite,
    inject_runtime_requirement,
    lower_metadata_requires_python,
    lower_sdist,
    post_load_backend,
)

STATIC = """\
    [project]
    name = "dummypkg"
    version = "0.0.0"
    requires-python = ">=3.15"

    [tool.retrofy]
    target-python = "3.9"
    """

MISSING = """\
    [project]
    name = "dummypkg"
    version = "0.0.0"

    [tool.retrofy]
    target-python = "3.9"
    """

DYNAMIC = """\
    [project]
    name = "dummypkg"
    version = "0.0.0"
    dynamic = ["requires-python"]

    [tool.retrofy]
    target-python = "3.9"
    """

METADATA = "Metadata-Version: 2.1\nName: dummypkg\nVersion: 0.0.0\n{line}\nBody.\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(pyprojecttoml, "load_file", pyprojecttoml.load_file)
    monkeypatch.chdir(tmp_path)

    def write(body: str) -> pathlib.Path:
        (tmp_path / "pyproject.toml").write_text(textwrap.dedent(body))
        return tmp_path

    return write


def _dist_info(tmp_path: pathlib.Path, requires_python: str) -> pathlib.Path:
    dist_info = tmp_path / "dummypkg-0.0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        METADATA.format(line=f"Requires-Python: {requires_python}\n"),
    )
    return dist_info


def _wheel(tmp_path: pathlib.Path, requires_python: str) -> pathlib.Path:
    whl = tmp_path / "dummypkg-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr(
            "dummypkg-0.0.0.dist-info/METADATA",
            METADATA.format(line=f"Requires-Python: {requires_python}\n"),
        )
        z.writestr("dummypkg-0.0.0.dist-info/top_level.txt", "dummypkg\n")
        z.writestr("dummypkg-0.0.0.dist-info/RECORD", "")
    return whl


def _sdist(tmp_path: pathlib.Path, pyproject: str) -> pathlib.Path:
    sdist = tmp_path / "dummypkg-0.0.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as tar:
        for name, content in {
            "pyproject.toml": textwrap.dedent(pyproject),
            "PKG-INFO": METADATA.format(line="Requires-Python: >=3.15\n"),
        }.items():
            data = content.encode()
            info = tarfile.TarInfo(f"dummypkg-0.0.0/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return sdist


HOOKS = {
    "post-load-backend": lambda tmp: post_load_backend(build_meta),
    "prepare-metadata-wheel": lambda tmp: lower_metadata_requires_python(
        _dist_info(tmp, ">=3.15"),
    ),
    "prepare-metadata-editable": lambda tmp: inject_runtime_requirement(
        _dist_info(tmp, ">=3.15"),
    ),
    "build-wheel": lambda tmp: compatibility_via_rewrite(_wheel(tmp, ">=3.15")),
    "build-editable": lambda tmp: compatibility_via_import_hook(_wheel(tmp, ">=3.15")),
}


@pytest.mark.parametrize("pyproject", [STATIC, MISSING], ids=["static", "missing"])
@pytest.mark.parametrize("hook", HOOKS)
def test_rejected_with_migration_instructions(project, tmp_path, pyproject, hook):
    project(pyproject)
    with pytest.raises(ValueError) as excinfo:
        HOOKS[hook](tmp_path)
    message = str(excinfo.value)
    assert 'dynamic = ["requires-python"]' in message
    assert "source-python" in message


@pytest.mark.parametrize("pyproject", [STATIC, MISSING], ids=["static", "missing"])
def test_sdist_rejected(project, tmp_path, pyproject):
    project(pyproject)
    sdist = _sdist(tmp_path, pyproject)
    with pytest.raises(ValueError, match=r'dynamic = \["requires-python"\]'):
        lower_sdist(sdist)


def test_metadata_requires_python_not_rewritten(project, tmp_path):
    # setuptools already wrote the supplied floor; the hooks leave it alone.
    project(DYNAMIC)
    dist_info = _dist_info(tmp_path, ">=3.9,!=3.10.*")
    lower_metadata_requires_python(dist_info)
    assert "Requires-Python: >=3.9,!=3.10.*\n" in (dist_info / "METADATA").read_text()


def test_static_requires_python_untouched_without_target_python(project, tmp_path):
    project(STATIC.split("[tool.retrofy]")[0])
    dist_info = _dist_info(tmp_path, ">=3.15")
    lower_metadata_requires_python(dist_info)
    assert "Requires-Python: >=3.15\n" in (dist_info / "METADATA").read_text()


def test_lowered_sdist_builds_with_retrofy_hooks(project, tmp_path, monkeypatch):
    # The lowered sdist's pyproject.toml has a static requires-python; when it
    # is built where retrofy's hooks run (retrofy's own sdist declares them
    # inline), that must not be rejected: it drops target-python.
    project(DYNAMIC)
    sdist = _sdist(tmp_path, DYNAMIC)
    lower_sdist(sdist)
    unpacked = tmp_path / "unpacked"
    with tarfile.open(sdist) as tar:
        tar.extractall(unpacked)
    monkeypatch.chdir(unpacked / "dummypkg-0.0.0")
    lower_metadata_requires_python(_dist_info(tmp_path, ">=3.9"))
