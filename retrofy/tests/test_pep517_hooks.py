from __future__ import annotations

import pathlib
import textwrap

import pytest

from retrofy import __version__ as RETROFY_VERSION
from retrofy._pep517_hooks import (
    EditableRuntimeRequirementError,
    _assert_editable_dependencies_dynamic,
    inject_runtime_requirement,
)

PINNED_REQUIRES = f"Requires-Dist: retrofy=={RETROFY_VERSION}"


def _write_metadata(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    dist_info = tmp_path / "some_project-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(textwrap.dedent(body), encoding="utf-8")
    return dist_info


def test_inject_runtime_requirement_adds_dep(tmp_path):
    dist_info = _write_metadata(
        tmp_path,
        """\
        Metadata-Version: 2.1
        Name: some-project
        Version: 0.1.0
        Requires-Dist: libcst

        Some long description.
        """,
    )
    inject_runtime_requirement(dist_info)
    text = (dist_info / "METADATA").read_text(encoding="utf-8")
    assert f"{PINNED_REQUIRES}\n" in text
    assert "Requires-Dist: libcst\n" in text
    assert text.endswith("Some long description.\n")
    # The line must land inside the header block (before the blank line),
    # otherwise pip parses it as part of the description.
    header, _, _ = text.partition("\n\n")
    assert PINNED_REQUIRES in header


def test_inject_runtime_requirement_adds_even_if_retrofy_already_present(tmp_path):
    # A pre-existing retrofy line may carry environment markers that
    # leave the running interpreter without retrofy installed, so we
    # add an unconditional line regardless. Pip dedupes the rest.
    dist_info = _write_metadata(
        tmp_path,
        """\
        Metadata-Version: 2.1
        Name: some-project
        Version: 0.1.0
        Requires-Dist: retrofy; python_version < "3.12"

        Body.
        """,
    )
    inject_runtime_requirement(dist_info)
    text = (dist_info / "METADATA").read_text(encoding="utf-8")
    header, _, _ = text.partition("\n\n")
    lines = [
        line
        for line in header.splitlines()
        if line.startswith("Requires-Dist: retrofy")
    ]
    assert len(lines) == 2
    assert PINNED_REQUIRES in lines


def test_inject_runtime_requirement_no_body(tmp_path):
    dist_info = _write_metadata(
        tmp_path,
        """\
        Metadata-Version: 2.1
        Name: some-project
        Version: 0.1.0
        """,
    )
    inject_runtime_requirement(dist_info)
    text = (dist_info / "METADATA").read_text(encoding="utf-8")
    assert PINNED_REQUIRES in text


def _write_pyproject(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_assert_editable_dependencies_dynamic_passes_when_dynamic(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "some-project"
        version = "0.1"
        dynamic = ["dependencies"]
        """,
    )
    _assert_editable_dependencies_dynamic(root)


def test_assert_editable_dependencies_dynamic_raises_when_static(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "some-project"
        version = "0.1"
        dependencies = ["retrofy"]
        """,
    )
    with pytest.raises(EditableRuntimeRequirementError, match="dynamic"):
        _assert_editable_dependencies_dynamic(root)


def test_assert_editable_dependencies_dynamic_raises_when_absent(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "some-project"
        version = "0.1"
        """,
    )
    with pytest.raises(EditableRuntimeRequirementError):
        _assert_editable_dependencies_dynamic(root)


def test_assert_editable_dependencies_dynamic_skips_when_no_pyproject(tmp_path):
    _assert_editable_dependencies_dynamic(tmp_path)
