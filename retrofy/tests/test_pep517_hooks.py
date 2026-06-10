from __future__ import annotations

import pathlib
import textwrap

import pytest

from retrofy._pep517_hooks import (
    EditableRuntimeRequirementError,
    _assert_editable_dependencies_dynamic,
    inject_runtime_requirement,
)


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
    assert "Requires-Dist: retrofy\n" in text
    assert "Requires-Dist: libcst\n" in text
    assert text.endswith("Some long description.\n")
    # The line must land inside the header block (before the blank line),
    # otherwise pip parses it as part of the description.
    header, _, _ = text.partition("\n\n")
    assert "Requires-Dist: retrofy" in header


def test_inject_runtime_requirement_is_idempotent(tmp_path):
    dist_info = _write_metadata(
        tmp_path,
        """\
        Metadata-Version: 2.1
        Name: some-project
        Version: 0.1.0
        Requires-Dist: retrofy

        Body.
        """,
    )
    before = (dist_info / "METADATA").read_text(encoding="utf-8")
    inject_runtime_requirement(dist_info)
    assert (dist_info / "METADATA").read_text(encoding="utf-8") == before


def test_inject_runtime_requirement_skips_when_retrofy_has_marker(tmp_path):
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
    before = (dist_info / "METADATA").read_text(encoding="utf-8")
    inject_runtime_requirement(dist_info)
    assert (dist_info / "METADATA").read_text(encoding="utf-8") == before


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
    assert "Requires-Dist: retrofy" in text


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
