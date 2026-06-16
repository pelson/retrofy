from __future__ import annotations

import pathlib
import textwrap
import zipfile

import pytest

from retrofy import __version__ as RETROFY_VERSION
from retrofy._pep517_hooks import (
    EditableRuntimeRequirementError,
    _assert_editable_dependencies_dynamic,
    _lower_requires_python,
    _read_target_python,
    compatibility_via_rewrite,
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


def test_lower_requires_python_replaces_existing_line():
    text = (
        "Metadata-Version: 2.1\n"
        "Name: retrofy\n"
        "Version: 0.4.0\n"
        "Requires-Python: >=3.15\n"
        "Requires-Dist: libcst\n"
        "\n"
        "Long description.\n"
    )
    new = _lower_requires_python(text, ">=3.9")
    assert "Requires-Python: >=3.9\n" in new
    assert "Requires-Python: >=3.15" not in new
    assert "Requires-Dist: libcst\n" in new
    assert new.endswith("Long description.\n")


def test_lower_requires_python_adds_when_missing():
    text = (
        "Metadata-Version: 2.1\n"
        "Name: retrofy\n"
        "Version: 0.4.0\n"
        "Requires-Dist: libcst\n"
        "\n"
        "Body.\n"
    )
    new = _lower_requires_python(text, ">=3.9")
    header, _, _ = new.partition("\n\n")
    assert "Requires-Python: >=3.9" in header


def test_lower_requires_python_only_touches_header_block():
    text = (
        "Metadata-Version: 2.1\n"
        "Name: retrofy\n"
        "Version: 0.4.0\n"
        "Requires-Python: >=3.15\n"
        "\n"
        "See Requires-Python: >=3.15 in the docs.\n"
    )
    new = _lower_requires_python(text, ">=3.9")
    header, _, body = new.partition("\n\n")
    assert "Requires-Python: >=3.9" in header
    assert "Requires-Python: >=3.15" not in header
    assert "Requires-Python: >=3.15" in body


def test_lower_requires_python_matches_case_insensitively():
    # PEP 566 headers are case-insensitive on the field name; a
    # lowercase ``requires-python:`` line must still be recognised
    # and replaced (with the canonical capitalisation).
    text = "Metadata-Version: 2.1\nName: retrofy\nrequires-python: >=3.15\n\nBody.\n"
    new = _lower_requires_python(text, ">=3.9")
    assert "Requires-Python: >=3.9" in new
    assert "requires-python: >=3.15" not in new
    assert "Requires-Python: >=3.15" not in new


def test_read_target_python_returns_floor_when_set(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "x"
        [tool.retrofy]
        target-python = "3.9"
        """,
    )
    assert _read_target_python(root) == ">=3.9"


def test_read_target_python_none_when_section_missing(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "x"
        """,
    )
    assert _read_target_python(root) is None


def test_read_target_python_none_when_key_missing(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "x"
        [tool.retrofy]
        """,
    )
    assert _read_target_python(root) is None


def test_read_target_python_none_when_pyproject_missing(tmp_path):
    assert _read_target_python(tmp_path) is None


def _make_minimal_wheel(tmp_path: pathlib.Path, metadata_text: str) -> pathlib.Path:
    whl = tmp_path / "dummypkg-0.0.0-py3-none-any.whl"
    dist_info = "dummypkg-0.0.0.dist-info"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr(f"{dist_info}/METADATA", metadata_text)
        z.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        z.writestr(f"{dist_info}/RECORD", "")
    return whl


def test_compatibility_via_rewrite_lowers_requires_python_when_opted_in(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    metadata = (
        "Metadata-Version: 2.1\n"
        "Name: dummypkg\n"
        "Version: 0.0.0\n"
        "Requires-Python: >=3.15\n"
        "\n"
        "Body.\n"
    )
    whl = _make_minimal_wheel(tmp_path, metadata)

    compatibility_via_rewrite(whl)

    with zipfile.ZipFile(whl) as z:
        new_metadata = z.read("dummypkg-0.0.0.dist-info/METADATA").decode("utf-8")
    header, _, _ = new_metadata.partition("\n\n")
    assert "Requires-Python: >=3.9" in header
    assert "Requires-Python: >=3.15" not in header


def test_compatibility_via_rewrite_leaves_requires_python_when_not_opted_in(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "dummypkg"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    metadata = (
        "Metadata-Version: 2.1\n"
        "Name: dummypkg\n"
        "Version: 0.0.0\n"
        "Requires-Python: >=3.15\n"
        "\n"
        "Body.\n"
    )
    whl = _make_minimal_wheel(tmp_path, metadata)

    compatibility_via_rewrite(whl)

    with zipfile.ZipFile(whl) as z:
        new_metadata = z.read("dummypkg-0.0.0.dist-info/METADATA").decode("utf-8")
    assert "Requires-Python: >=3.15" in new_metadata
    assert "Requires-Python: >=3.9" not in new_metadata


def test_compatibility_via_rewrite_skipped_when_disable_env_set(
    tmp_path,
    monkeypatch,
):
    # Bootstrap escape hatch: with ``RETROFY_DISABLE_REWRITE=1``, the
    # hook must be a complete no-op even when the project opts in to
    # rewriting via ``[tool.retrofy] target-python``.
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RETROFY_DISABLE_REWRITE", "1")

    raw_source = "lazy from foo import bar\n"
    raw_metadata = (
        "Metadata-Version: 2.1\n"
        "Name: dummypkg\n"
        "Version: 0.0.0\n"
        "Requires-Python: >=3.15\n"
        "\n"
        "Body.\n"
    )
    whl = _make_minimal_wheel(tmp_path, raw_metadata)
    # Stuff a raw-lazy .py into the wheel so we'd notice if convert
    # ran anyway.
    with zipfile.ZipFile(whl, "a") as z:
        z.writestr("dummypkg/x.py", raw_source)

    compatibility_via_rewrite(whl)

    with zipfile.ZipFile(whl) as z:
        py = z.read("dummypkg/x.py").decode("utf-8")
        md = z.read("dummypkg-0.0.0.dist-info/METADATA").decode("utf-8")
    assert py == raw_source  # not converted
    assert "Requires-Python: >=3.15" in md  # METADATA not lowered
